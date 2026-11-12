"""Business logic for attachments."""

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from .models import Attachment


def attachments_for(obj, include_history=False):
    """Attachments linked to any record. Hides superseded and archived files by default."""
    queryset = Attachment.objects.filter(
        content_type=ContentType.objects.get_for_model(obj), object_id=obj.pk
    )
    if not include_history:
        queryset = queryset.filter(is_current=True, is_archived=False)
    return queryset


def attach(obj, file, category=Attachment.Category.OTHER, user=None, notes="", revision=""):
    attachment = Attachment(
        parent=obj, file=file, category=category, uploaded_by=user, notes=notes, revision=revision
    )
    attachment.full_clean()
    attachment.save()
    return attachment


@transaction.atomic
def supersede(old: Attachment, file, revision, user=None, notes=""):
    """Upload a new revision of a drawing. The old one stays in history."""
    new = Attachment(
        content_type=old.content_type,
        object_id=old.object_id,
        file=file,
        category=old.category,
        revision=revision,
        supersedes=old,
        uploaded_by=user,
        notes=notes,
    )
    new.full_clean()
    new.save()
    old.is_current = False
    old.save(update_fields=["is_current"])
    return new
