"""Business logic for jobs. Views, admin and the API should call these, not edit models directly."""

from django.db import transaction

from .models import JobItem, SpecRevision


@transaction.atomic
def update_spec(item: JobItem, spec: dict, user=None, note: str = "") -> JobItem:
    """Change an item's spec, keeping the previous version in SpecRevision."""
    item = JobItem.objects.select_for_update().get(pk=item.pk)
    if spec == item.spec:
        return item
    SpecRevision.objects.create(
        item=item, version=item.spec_version, spec=item.spec, changed_by=user, note=note
    )
    item.spec = spec
    item.spec_version += 1
    item.save(update_fields=["spec", "spec_version"])
    return item
