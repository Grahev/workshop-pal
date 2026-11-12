"""Read-side queries for the shop floor. Kept apart from services, which change state."""

from django.utils import timezone

from routing.models import Operation

Status = Operation.Status


def annotate_readiness(op, today=None):
    """Attach what an open operation is waiting for, for the queue and board to show.

    waiting_on      strict earlier steps not done: Start is blocked
    waiting_gates   gates not yet cleared: Start is blocked
    finish_waiting  steps that must finish before this one can be completed
    earlier_steps   advisory earlier steps not done: shown, not enforced
    """
    readiness = op.readiness()
    op.waiting_on = readiness.start_blockers
    op.waiting_gates = readiness.gate_blockers
    op.finish_waiting = readiness.finish_blockers
    op.earlier_steps = readiness.warnings
    op.can_start = readiness.can_start
    due = op.item.job.due_date
    op.is_late = bool(due and due < (today or timezone.localdate()))
    return op


def queue_group(op):
    """Which part of a queue an open operation belongs in."""
    if op.status in (Status.IN_PROGRESS, Status.PAUSED):
        return "active"
    if op.status == Status.ON_HOLD:
        return "on_hold"
    if op.waiting_on or op.waiting_gates or op.earlier_steps:
        return "upcoming"
    return "ready"


def station_queue(work_centre, query=""):
    """Open operations at a work centre, grouped for the workstation screen.

    active    in progress or paused here
    on_hold   held, with reason
    ready     pending, with nothing in front of it
    upcoming  pending, but waiting on an earlier step or a gate
    """
    operations = (
        Operation.objects.filter(work_centre=work_centre, item__job__is_archived=False)
        .exclude(status__in=Operation.DONE_STATUSES)
        .select_related("item__job__customer", "hold_reason", "work_centre")
        .prefetch_related("dependencies__predecessor", "gates__gate_type")
        .order_by("-item__job__priority", "item__job__due_date", "item__job__reference", "sequence")
    )
    if query:
        operations = operations.filter(item__job__reference__icontains=query)

    today = timezone.localdate()
    queue = {"active": [], "on_hold": [], "ready": [], "upcoming": []}
    for op in operations:
        annotate_readiness(op, today)
        queue[queue_group(op)].append(op)
    return queue


def spec_rows(item):
    """An item's spec as (label, value) pairs, using the product type's field labels where defined."""
    labels = {}
    if item.product_type:
        for field in item.product_type.spec_fields or []:
            if isinstance(field, dict) and "key" in field:
                labels[field["key"]] = field.get("label") or field["key"]
    ordered_keys = [key for key in labels if key in item.spec]
    ordered_keys += [key for key in item.spec if key not in labels]
    return [(labels.get(key, key.replace("_", " ").capitalize()), item.spec[key]) for key in ordered_keys]
