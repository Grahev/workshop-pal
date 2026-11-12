from django.db import models


class RouteTemplate(models.Model):
    """A reusable starting route for a product family. Copied onto an item, then edited per job."""

    name = models.CharField(max_length=100)
    product_type = models.ForeignKey(
        "jobs.ProductType", on_delete=models.CASCADE, null=True, blank=True, related_name="route_templates"
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class RouteTemplateStep(models.Model):
    template = models.ForeignKey(RouteTemplate, on_delete=models.CASCADE, related_name="steps")
    sequence = models.PositiveIntegerField()
    name = models.CharField(max_length=100)
    work_centre = models.ForeignKey("resources.WorkCentre", on_delete=models.PROTECT, related_name="+")
    estimated_minutes = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["template", "sequence"]

    def __str__(self):
        return f"{self.template} · {self.sequence} {self.name}"


class HoldReason(models.Model):
    """Why an operation cannot proceed: waiting on material, drawing, approval, machine down..."""

    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Operation(models.Model):
    """One stage of work on one job item at one work centre.

    `status` and the timestamp fields are a cached projection of the event log.
    They are only ever changed by floor.services.record_event, never edited directly.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In progress"
        PAUSED = "paused", "Paused"
        ON_HOLD = "on_hold", "On hold"
        COMPLETED = "completed", "Completed"
        SKIPPED = "skipped", "Skipped"

    DONE_STATUSES = (Status.COMPLETED, Status.SKIPPED)

    item = models.ForeignKey("jobs.JobItem", on_delete=models.CASCADE, related_name="operations")
    sequence = models.PositiveIntegerField()
    name = models.CharField(max_length=100)
    work_centre = models.ForeignKey("resources.WorkCentre", on_delete=models.PROTECT, related_name="operations")
    estimated_minutes = models.PositiveIntegerField(default=0)
    depends_on = models.ManyToManyField(
        "self", symmetrical=False, blank=True, related_name="dependents",
        help_text="Operations that must be completed or skipped before this one can start",
    )

    # Projection of the event log (read-only outside floor.services)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    held_from = models.CharField(max_length=20, choices=Status.choices, blank=True)
    hold_reason = models.ForeignKey(HoldReason, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    status_changed_at = models.DateTimeField(null=True, blank=True)
    rework_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["item", "sequence"]
        indexes = [models.Index(fields=["work_centre", "status"])]

    def __str__(self):
        return f"{self.item} · {self.sequence} {self.name}"

    @property
    def is_done(self):
        return self.status in self.DONE_STATUSES

    def blocking_dependencies(self):
        """Dependencies that are not yet completed or skipped."""
        return self.depends_on.exclude(status__in=self.DONE_STATUSES)
