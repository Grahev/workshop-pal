"""Read-side queries for the shop floor. Kept apart from services, which change state."""

from collections import defaultdict

from django.utils import timezone

from routing.models import Operation

Status = Operation.Status


def station_queue(work_centre, query=""):
    """Open operations at a work centre, grouped for the workstation screen.

    Returns a dict with four lists:
      active    in progress or paused here
      on_hold   held, with reason
      ready     pending and nothing in front of it
      upcoming  pending, but an earlier step on the item is not finished
                or a hard dependency is outstanding
    Each operation gets `is_late`, `waiting_on` (hard dependencies, block Start)
    and `earlier_steps` (earlier unfinished steps, information only).
    """
    operations = (
        Operation.objects.filter(work_centre=work_centre, item__job__is_archived=False)
        .exclude(status__in=Operation.DONE_STATUSES)
        .select_related("item__job__customer", "hold_reason", "work_centre")
        .prefetch_related("depends_on")
        .order_by("-item__job__priority", "item__job__due_date", "item__job__reference", "sequence")
    )
    if query:
        operations = operations.filter(item__job__reference__icontains=query)
    operations = list(operations)

    # Unfinished steps on the same items, to tell operators what is still in front.
    open_by_item = defaultdict(list)
    siblings = (
        Operation.objects.filter(item_id__in={op.item_id for op in operations})
        .exclude(status__in=Operation.DONE_STATUSES)
        .values_list("item_id", "sequence", "name")
    )
    for item_id, sequence, name in siblings:
        open_by_item[item_id].append((sequence, name))

    today = timezone.localdate()
    queue = {"active": [], "on_hold": [], "ready": [], "upcoming": []}
    for op in operations:
        due = op.item.job.due_date
        op.is_late = bool(due and due < today)
        op.waiting_on = [dep.name for dep in op.depends_on.all() if not dep.is_done]
        op.earlier_steps = [name for seq, name in sorted(open_by_item[op.item_id]) if seq < op.sequence]

        if op.status in (Status.IN_PROGRESS, Status.PAUSED):
            queue["active"].append(op)
        elif op.status == Status.ON_HOLD:
            queue["on_hold"].append(op)
        elif op.waiting_on or op.earlier_steps:
            queue["upcoming"].append(op)
        else:
            queue["ready"].append(op)
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
