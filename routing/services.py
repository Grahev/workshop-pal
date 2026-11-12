"""Business logic for routes."""

from django.db import transaction
from django.db.models import Max

from .models import Operation, RouteTemplate


class RouteError(Exception):
    """The requested change to a route is not allowed."""


@transaction.atomic
def apply_template(item, template: RouteTemplate) -> list[Operation]:
    """Copy a template's steps onto an item as operations. The copies are then free to be edited."""
    operations = [
        Operation(
            item=item,
            sequence=step.sequence,
            name=step.name,
            work_centre=step.work_centre,
            estimated_minutes=step.estimated_minutes,
        )
        for step in template.steps.select_related("work_centre").order_by("sequence")
    ]
    return Operation.objects.bulk_create(operations)


def add_operation(item, name, work_centre, estimated_minutes=0) -> Operation:
    """Add an operation to the end of an item's route."""
    last = item.operations.aggregate(last=Max("sequence"))["last"] or 0
    return Operation.objects.create(
        item=item, sequence=last + 10, name=name, work_centre=work_centre, estimated_minutes=estimated_minutes
    )


@transaction.atomic
def move_operation(operation, direction) -> None:
    """Swap an operation with its neighbour. direction is 'up' or 'down'."""
    siblings = list(operation.item.operations.select_for_update().order_by("sequence", "id"))
    index = next(i for i, op in enumerate(siblings) if op.pk == operation.pk)
    target = index - 1 if direction == "up" else index + 1
    if target < 0 or target >= len(siblings):
        return
    siblings[index], siblings[target] = siblings[target], siblings[index]
    # Renumber the whole route so sequences stay unique and evenly spaced.
    for position, op in enumerate(siblings, start=1):
        if op.sequence != position * 10:
            op.sequence = position * 10
            op.save(update_fields=["sequence"])


def remove_operation(operation) -> None:
    """Delete an operation that nothing has happened to yet. Anything with history is skipped instead."""
    if operation.status != Operation.Status.PENDING or operation.events.exists():
        raise RouteError("This step has history, so it cannot be removed. Skip it instead.")
    operation.delete()


def update_operation(operation, name, work_centre, estimated_minutes) -> Operation:
    """Change the details of a step. Only before work has started on it."""
    if operation.status != Operation.Status.PENDING:
        raise RouteError("Only steps that have not started can be changed.")
    operation.name = name
    operation.work_centre = work_centre
    operation.estimated_minutes = estimated_minutes
    operation.save(update_fields=["name", "work_centre", "estimated_minutes"])
    return operation
