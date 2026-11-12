from django.conf import settings
from django.db import models

from routing.models import Enforcement


class Customer(models.Model):
    name = models.CharField(max_length=200, unique=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ProductType(models.Model):
    """A product family. Defines which spec fields a bespoke item of this type carries."""

    name = models.CharField(max_length=100, unique=True)
    spec_fields = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            'List of field definitions, e.g. '
            '[{"key": "width_mm", "label": "Width (mm)", "type": "number", "required": true}]'
        ),
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Job(models.Model):
    """A customer order."""

    class Priority(models.IntegerChoices):
        LOW = 10, "Low"
        NORMAL = 20, "Normal"
        HIGH = 30, "High"
        URGENT = 40, "Urgent"

    reference = models.CharField(max_length=50, unique=True, help_text="Job number printed on the job sheet")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="jobs")
    site = models.ForeignKey("resources.Site", on_delete=models.PROTECT, related_name="jobs")
    title = models.CharField(max_length=200)
    customer_reference = models.CharField(max_length=100, blank=True, help_text="Customer PO or order number")
    due_date = models.DateField(null=True, blank=True)
    priority = models.IntegerField(choices=Priority.choices, default=Priority.NORMAL)
    notes = models.TextField(blank=True)
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-priority", "due_date", "reference"]

    def __str__(self):
        return f"{self.reference} · {self.title}"


class JobItem(models.Model):
    """One thing being made within a job. Carries its own bespoke spec and its own route."""

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="items")
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="children",
        help_text="Set when this item is a sub-assembly of another item",
    )
    product_type = models.ForeignKey(
        ProductType, on_delete=models.PROTECT, null=True, blank=True, related_name="items"
    )
    name = models.CharField(max_length=200)
    quantity = models.PositiveIntegerField(default=1)
    spec = models.JSONField(default=dict, blank=True, help_text="Bespoke values keyed by the product type's spec fields")
    spec_version = models.PositiveIntegerField(default=1)
    link_enforcement = models.CharField(
        "default link enforcement", max_length=10, choices=Enforcement.choices, default=Enforcement.STRICT,
        help_text="Default for new links between steps on this item's route.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["job", "id"]

    def __str__(self):
        return f"{self.job.reference} / {self.name}"


class SpecRevision(models.Model):
    """History of spec changes for an item. Written by jobs.services.update_spec."""

    item = models.ForeignKey(JobItem, on_delete=models.CASCADE, related_name="spec_revisions")
    version = models.PositiveIntegerField()
    spec = models.JSONField()
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    changed_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["item", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["item", "version"], name="unique_spec_version_per_item"),
        ]

    def __str__(self):
        return f"{self.item} spec v{self.version}"
