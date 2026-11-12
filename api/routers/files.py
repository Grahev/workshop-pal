"""Attachments: drawings, photos, NCR documents and other files."""

from django.shortcuts import get_object_or_404
from ninja import File, Form, Router
from ninja.errors import HttpError
from ninja.files import UploadedFile

from attachments import services
from attachments.models import Attachment
from floor.models import Issue
from jobs.models import Job, JobItem
from routing.models import Operation

from .. import schemas
from ..auth import require_manage

router = Router(tags=["Attachments"])

TARGETS = {"job": Job, "item": JobItem, "operation": Operation, "issue": Issue}
TARGET_NAME = {model._meta.model_name: name for name, model in TARGETS.items()}


def _target(target_type, target_id):
    return get_object_or_404(TARGETS[target_type], pk=target_id)


def _out(request, attachment):
    return {
        "id": attachment.pk,
        "target_type": TARGET_NAME.get(attachment.content_type.model, attachment.content_type.model),
        "target_id": attachment.object_id,
        "filename": attachment.original_filename,
        "url": request.build_absolute_uri(attachment.file.url),
        "size": attachment.size,
        "category": attachment.category,
        "revision": attachment.revision,
        "is_current": attachment.is_current,
        "is_archived": attachment.is_archived,
        "notes": attachment.notes,
        "uploaded_by": attachment.uploaded_by.username if attachment.uploaded_by_id else None,
        "uploaded_at": attachment.uploaded_at,
    }


def _check_category(category):
    if category not in Attachment.Category.values:
        raise HttpError(422, f"category must be one of: {', '.join(Attachment.Category.values)}.")


@router.get("/attachments", response=list[schemas.AttachmentOut], summary="List the files on a job, item, operation or issue")
def list_attachments(request, target_type: schemas.TargetType, target_id: int, include_history: bool = False):
    """Current files only by default. `include_history` adds superseded revisions and archived files."""
    attachments = services.attachments_for(_target(target_type, target_id), include_history=include_history)
    return [_out(request, a) for a in attachments.select_related("content_type", "uploaded_by")]


@router.post("/attachments", response={201: schemas.AttachmentOut}, summary="Upload a file")
def upload_attachment(
    request,
    target_type: Form[schemas.TargetType],
    target_id: Form[int],
    file: UploadedFile = File(...),
    category: Form[str] = "other",
    revision: Form[str] = "",
    notes: Form[str] = "",
):
    """Send as `multipart/form-data`. Category is one of drawing, photo, ncr, certificate, customer_doc, other."""
    _check_category(category)
    attachment = services.attach(
        _target(target_type, target_id), file, category=category, user=request.user, notes=notes, revision=revision
    )
    return 201, _out(request, attachment)


@router.post("/attachments/{attachment_id}/supersede", response={201: schemas.AttachmentOut}, summary="Upload a new revision of a drawing")
def supersede_attachment(request, attachment_id: int, revision: Form[str], file: UploadedFile = File(...), notes: Form[str] = ""):
    """The old revision stays in history and stops being current."""
    require_manage(request)
    old = get_object_or_404(Attachment, pk=attachment_id)
    if not old.is_current:
        raise HttpError(409, "This file has already been superseded.")
    return 201, _out(request, services.supersede(old, file, revision=revision, user=request.user, notes=notes))


@router.post("/attachments/{attachment_id}/archive", response=schemas.AttachmentOut, summary="Archive a file")
def archive_attachment(request, attachment_id: int):
    """Hides the file from normal lists. Nothing is ever deleted."""
    require_manage(request)
    attachment = get_object_or_404(Attachment, pk=attachment_id)
    attachment.is_archived = True
    attachment.save(update_fields=["is_archived"])
    return _out(request, attachment)
