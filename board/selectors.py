"""Read-side queries for the live board. Everything here is derived from operations, never stored."""

from types import SimpleNamespace

from django.conf import settings
from django.db.models import Prefetch, Q
from django.utils import timezone

from jobs.models import JobItem
from resources.models import WorkCentre
from routing.models import Operation

Status = Operation.Status

# Item states shown on the board, in addition to the operation statuses.
NO_ROUTE = "no_route"
STATE_LABELS = {
    Status.IN_PROGRESS: "In progress",
    Status.PAUSED: "Paused",
    Status.ON_HOLD: "On hold",
    Status.PENDING: "Waiting",
    Status.COMPLETED: "Completed",
    NO_ROUTE: "No route",
}
# When an item has several open operations, the first of these found decides its state.
STATE_PRECEDENCE = [Status.ON_HOLD, Status.IN_PROGRESS, Status.PAUSED, Status.PENDING]


def _operations_prefetch():
    return Prefetch(
        "operations",
        queryset=Operation.objects.select_related("work_centre", "hold_reason")
        .prefetch_related("depends_on")
        .order_by("sequence"),
    )


def build_row(item, today=None):
    """Summarise one job item: where it is, what state it is in, and whether it is late."""
    today = today or timezone.localdate()
    operations = list(item.operations.all())
    open_operations = [op for op in operations if not op.is_done]
    done = len(operations) - len(open_operations)

    current = None
    if not operations:
        state = NO_ROUTE
    elif not open_operations:
        state = Status.COMPLETED
    else:
        for candidate in STATE_PRECEDENCE:
            current = next((op for op in open_operations if op.status == candidate), None)
            if current:
                state = candidate
                break

    due = item.job.due_date
    finished = state == Status.COMPLETED
    days_left = (due - today).days if due else None
    is_late = bool(due and not finished and days_left < 0)
    at_risk = bool(due and not finished and not is_late and days_left <= settings.BOARD_AT_RISK_DAYS)

    return SimpleNamespace(
        item=item,
        job=item.job,
        operations=operations,
        current=current,
        state=state,
        state_label=STATE_LABELS[state],
        done=done,
        total=len(operations),
        percent=round(100 * done / len(operations)) if operations else 0,
        is_late=is_late,
        at_risk=at_risk,
        held_since=current.status_changed_at if current and state == Status.ON_HOLD else None,
    )


def _items(query="", customer_id=None):
    items = (
        JobItem.objects.filter(job__is_archived=False)
        .select_related("job__customer", "product_type")
        .prefetch_related(_operations_prefetch())
        .order_by("-job__priority", "job__due_date", "job__reference", "id")
    )
    if query:
        items = items.filter(
            Q(job__reference__icontains=query)
            | Q(job__title__icontains=query)
            | Q(name__icontains=query)
            | Q(job__customer__name__icontains=query)
        )
    if customer_id:
        items = items.filter(job__customer_id=customer_id)
    return items


def board_rows(query="", customer_id=None, work_centre_id=None, status=""):
    """Rows for the board plus headline counts.

    Counts cover every active item matching the search, customer and work centre,
    so the tiles stay stable while the status filter narrows the list underneath.
    """
    today = timezone.localdate()
    rows = [build_row(item, today) for item in _items(query, customer_id)]
    if work_centre_id:
        rows = [row for row in rows if row.current and row.current.work_centre_id == work_centre_id]

    active = [row for row in rows if row.state != Status.COMPLETED]
    summary = {
        "active": len(active),
        "in_progress": sum(row.state == Status.IN_PROGRESS for row in active),
        "on_hold": sum(row.state == Status.ON_HOLD for row in active),
        "late": sum(row.is_late for row in active),
        "at_risk": sum(row.at_risk for row in active),
    }

    if status == "attention":
        rows = [row for row in active if row.is_late or row.at_risk or row.state == Status.ON_HOLD]
    elif status == "late":
        rows = [row for row in active if row.is_late]
    elif status:
        rows = [row for row in rows if row.state == status]
    else:
        rows = active
    return rows, summary


def board_lanes(query="", customer_id=None, work_centre_id=None):
    """Open operations grouped by work centre: running, on hold, ready, and coming up."""
    today = timezone.localdate()
    work_centres = WorkCentre.objects.filter(is_active=True, site__is_active=True)
    if work_centre_id:
        work_centres = work_centres.filter(pk=work_centre_id)
    lanes = {
        wc.pk: SimpleNamespace(work_centre=wc, active=[], on_hold=[], ready=[], upcoming=[])
        for wc in work_centres
    }

    for item in _items(query, customer_id):
        due = item.job.due_date
        open_seen = []
        for op in item.operations.all():
            if op.is_done:
                continue
            lane = lanes.get(op.work_centre_id)
            if lane is not None:
                op.is_late = bool(due and due < today)
                op.waiting_on = [dep.name for dep in op.depends_on.all() if not dep.is_done]
                op.earlier_steps = list(open_seen)
                if op.status in (Status.IN_PROGRESS, Status.PAUSED):
                    lane.active.append(op)
                elif op.status == Status.ON_HOLD:
                    lane.on_hold.append(op)
                elif op.waiting_on or op.earlier_steps:
                    lane.upcoming.append(op)
                else:
                    lane.ready.append(op)
            open_seen.append(op.name)
    return list(lanes.values())
