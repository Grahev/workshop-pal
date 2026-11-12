from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from accounts.permissions import office_required, safe_next

from . import services
from .models import Attachment

# Records that files may be attached to.
ATTACHABLE = {("jobs", "job"), ("jobs", "jobitem"), ("routing", "operation"), ("floor", "issue")}


def _error_text(exc):
    return " ".join(exc.messages) if hasattr(exc, "messages") else str(exc)


@login_required
@require_POST
def upload(request):
    """Attach a file to a job, item, operation or issue. Anyone signed in can add files."""
    content_type = get_object_or_404(ContentType, pk=request.POST.get("content_type") or 0)
    if (content_type.app_label, content_type.model) not in ATTACHABLE:
        return HttpResponseBadRequest("Files cannot be attached to this record.")
    target = get_object_or_404(content_type.model_class(), pk=request.POST.get("object_id") or 0)

    file = request.FILES.get("file")
    category = request.POST.get("category") or Attachment.Category.OTHER
    if not file:
        messages.error(request, "Choose a file first.")
    elif category not in Attachment.Category.values:
        messages.error(request, "Unknown file category.")
    else:
        try:
            services.attach(
                target, file, category=category, user=request.user,
                notes=request.POST.get("notes", "").strip(),
                revision=request.POST.get("revision", "").strip(),
            )
            messages.success(request, f"{file.name} attached.")
        except ValidationError as exc:
            messages.error(request, _error_text(exc))
    return redirect(safe_next(request, "/"))


@office_required
@require_POST
def supersede(request, pk):
    """Upload a new revision of a drawing. The old revision stays in history."""
    old = get_object_or_404(Attachment, pk=pk, is_current=True)
    file = request.FILES.get("file")
    revision = request.POST.get("revision", "").strip()
    if not file or not revision:
        messages.error(request, "A new revision needs a file and a revision label.")
    else:
        try:
            services.supersede(old, file, revision=revision, user=request.user)
            messages.success(request, f"Revision {revision} is now current.")
        except ValidationError as exc:
            messages.error(request, _error_text(exc))
    return redirect(safe_next(request, "/"))


@office_required
@require_POST
def archive(request, pk):
    """Hide a file from normal view. Nothing is deleted."""
    attachment = get_object_or_404(Attachment, pk=pk)
    attachment.is_archived = True
    attachment.save(update_fields=["is_archived"])
    messages.success(request, "File archived.")
    return redirect(safe_next(request, "/"))
