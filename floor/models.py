from django.conf import settings
from django.db import models
from django.utils import timezone


class Event(models.Model):
    """An immutable record of something that happened on the shop floor.

    The event log is the source of truth. Operation.status is derived from it.
    Create events through floor.services, never directly.
    """

    class Type(models.TextChoices):
        START = "start", "Started"
        PAUSE = "pause", "Paused"
        RESUME = "resume", "Resumed"
        COMPLETE = "complete", "Completed"
        HOLD = "hold", "Put on hold"
        RELEASE = "release", "Released from hold"
        REWORK = "rework", "Sent back for rework"
        SKIP = "skip", "Skipped"
        NOTE = "note", "Note"

    operation = models.ForeignKey("routing.Operation", on_delete=models.PROTECT, related_name="events")
    type = models.CharField(max_length=20, choices=Type.choices)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="floor_events"
    )
    work_centre = models.ForeignKey(
        "resources.WorkCentre", on_delete=models.PROTECT, null=True, blank=True, related_name="+",
        help_text="Workstation the event was recorded at",
    )
    hold_reason = models.ForeignKey(
        "routing.HoldReason", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    note = models.TextField(blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["occurred_at", "id"]

    def __str__(self):
        return f"{self.get_type_display()} · {self.operation}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise TypeError("Events are immutable. Record a new event to correct a mistake.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise TypeError("Events cannot be deleted.")


class Issue(models.Model):
    """A problem raised against an operation. An NCR is an issue with category NCR (PRD T12)."""

    class Category(models.TextChoices):
        ISSUE = "issue", "Issue"
        NCR = "ncr", "Non-conformance (NCR)"

    operation = models.ForeignKey("routing.Operation", on_delete=models.PROTECT, related_name="issues")
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.ISSUE)
    description = models.TextField()
    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="issues_raised"
    )
    raised_at = models.DateTimeField(auto_now_add=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="issues_resolved"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution = models.TextField(blank=True)

    class Meta:
        ordering = ["-raised_at"]

    def __str__(self):
        return f"{self.get_category_display()} on {self.operation}"

    @property
    def is_open(self):
        return self.resolved_at is None
