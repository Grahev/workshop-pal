from django import template
from django.contrib.contenttypes.models import ContentType

from ..models import Attachment
from ..services import attachments_for

register = template.Library()

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")


@register.inclusion_tag("attachments/_panel.html", takes_context=True)
def attachment_panel(context, target, title="Files"):
    """Files attached to `target`, with upload, camera capture and drawing revision controls."""
    request = context["request"]
    return {
        "title": title,
        "attachments": attachments_for(target).select_related("uploaded_by"),
        "history_count": attachments_for(target, include_history=True).filter(is_current=False).count(),
        "target_ct": ContentType.objects.get_for_model(target).pk,
        "target_id": target.pk,
        "categories": Attachment.Category.choices,
        "next": request.get_full_path(),
        "can_manage": context.get("can_manage", False),
        "csrf_token": context.get("csrf_token"),
    }


@register.filter
def attachments(target):
    """Current attachments for any record: {% for file in issue|attachments %}"""
    return attachments_for(target)


@register.filter
def is_image(attachment):
    return attachment.file.name.lower().endswith(IMAGE_EXTENSIONS)
