"""Business logic for routes."""

from django.db import transaction

from .models import Operation, RouteTemplate


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
