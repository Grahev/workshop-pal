import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models


def attachment_path(instance, filename):
    """media/attachments/<model>/<object id>/<uuid>_<filename>"""
    safe_name = Path(filename).name
    return f"attachments/{instance.content_type.model}/{instance.object_id}/{uuid.uuid4().hex[:8]}_{safe_name}"


def validate_attachment(file):
    extension = Path(file.name).suffix.lower().lstrip(".")
    if extension not in settings.ATTACHMENT_ALLOWED_EXTENSIONS:
        raise ValidationError(f"Files of type .{extension} are not allowed.")
    if file.size > settings.ATTACHMENT_MAX_BYTES:
        limit_mb = settings.ATTACHMENT_MAX_BYTES // (1024 * 1024)
        raise ValidationError(f"File is larger than the {limit_mb} MB limit.")


class Attachment(models.Model):
    """A file linked to any record: job, item, operation, issue, and later NCRs.

    Files are never deleted. Drawings are superseded by a newer revision;
    anything else can be archived.
    """

    class Category(models.TextChoices):
        DRAWING = "drawing", "Drawing"
        PHOTO = "photo", "Photo"
        NCR = "ncr", "NCR"
        CERTIFICATE = "certificate", "Certificate"
        CUSTOMER_DOC = "customer_doc", "Customer document"
        OTHER = "other", "Other"

    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.PositiveBigIntegerField()
    parent = GenericForeignKey("content_type", "object_id")

    file = models.FileField(upload_to=attachment_path, validators=[validate_attachment], max_length=500)
    original_filename = models.CharField(max_length=255, editable=False)
    size = models.PositiveBigIntegerField(editable=False, default=0)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.OTHER)
    notes = models.TextField(blank=True)

    # Drawings
    revision = models.CharField(max_length=20, blank=True, help_text="Drawing revision, e.g. A, B, 02")
    is_current = models.BooleanField(default=True, db_index=True)
    supersedes = models.OneToOneField(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="superseded_by"
    )

    is_archived = models.BooleanField(default=False)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        label = self.original_filename or Path(self.file.name).name
        return f"{label} (rev {self.revision})" if self.revision else label

    def save(self, *args, **kwargs):
        if self._state.adding and self.file:
            self.original_filename = self.original_filename or Path(self.file.name).name
            self.size = self.file.size
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise TypeError("Attachments are never deleted. Archive or supersede instead.")
