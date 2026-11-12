from types import SimpleNamespace

from django.conf import settings
from django.db import models


class Enforcement(models.TextChoices):
    STRICT = "strict", "Strict: blocks until done"
    ADVISORY = "advisory", "Advisory: warns only"


class LinkKind(models.TextChoices):
    FINISH_TO_START = "finish_to_start", "Must finish before this starts"
    FINISH_TO_FINISH = "finish_to_finish", "This can start, but cannot complete until that finishes"


class GateType(models.Model):
    """A kind of condition that is not a work step: hardware available, material in, drawing approved."""

    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class RouteTemplate(models.Model):
    """A reusable starting route for a product family. Copied onto an item, then edited per job."""

    name = models.CharField(max_length=100)
    product_type = models.ForeignKey(
        "jobs.ProductType", on_delete=models.CASCADE, null=True, blank=True, related_name="route_templates"
    )
    simple_sequence = models.BooleanField(
        default=True,
        help_text="Each step follows the one before. Untick to set the links between steps by hand.",
    )
    default_enforcement = models.CharField(
        max_length=10, choices=Enforcement.choices, default=Enforcement.STRICT,
        help_text="Used for the sequence links, and as the default for links added to the route later.",
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
    gate_types = models.ManyToManyField(
        GateType, blank=True, related_name="+", verbose_name="needs",
        help_text="Conditions that must be met before this step can start",
    )

    class Meta:
        ordering = ["template", "sequence"]

    def __str__(self):
        return f"{self.sequence} {self.name}"


class RouteTemplateLink(models.Model):
    """`step` comes after `predecessor`. Only used when the template is not a simple sequence."""

    template = models.ForeignKey(RouteTemplate, on_delete=models.CASCADE, related_name="links")
    step = models.ForeignKey(RouteTemplateStep, on_delete=models.CASCADE, related_name="links")
    predecessor = models.ForeignKey(
        RouteTemplateStep, on_delete=models.CASCADE, related_name="+", verbose_name="comes after"
    )
    kind = models.CharField("type", max_length=20, choices=LinkKind.choices, default=LinkKind.FINISH_TO_START)
    enforcement = models.CharField(max_length=10, choices=Enforcement.choices, default=Enforcement.STRICT)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["step", "predecessor"], name="unique_template_link"),
        ]

    def __str__(self):
        return f"{self.step.name} after {self.predecessor.name}"


class HoldReason(models.Model):
    """Why an operation cannot proceed: waiting on material, drawing, approval, machine down..."""

    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Gate(models.Model):
    """A condition on one job item, e.g. 'Hardware' for this item is waiting or available.

    Operations that need it cannot start until it is cleared. Unlike a hold,
    which reacts to a problem, a gate is planned into the route up front.
    """

    item = models.ForeignKey("jobs.JobItem", on_delete=models.CASCADE, related_name="gates")
    gate_type = models.ForeignKey(GateType, on_delete=models.PROTECT, related_name="gates")
    is_cleared = models.BooleanField(default=False)
    expected_date = models.DateField(null=True, blank=True)
    cleared_at = models.DateTimeField(null=True, blank=True)
    cleared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["item", "gate_type__name"]
        constraints = [
            models.UniqueConstraint(fields=["item", "gate_type"], name="unique_gate_per_item"),
        ]

    def __str__(self):
        return f"{self.gate_type.name} for {self.item}"

    @property
    def name(self):
        return self.gate_type.name


class Operation(models.Model):
    """One stage of work on one job item at one work centre.

    A route is a network: each operation lists the operations it comes after
    (`dependencies`) and the gates it needs. `sequence` is display order only.

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
    gates = models.ManyToManyField(Gate, blank=True, related_name="operations")

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

    def readiness(self):
        """What, if anything, this operation is waiting for.

        start_blockers   strict "finish before start" predecessors not yet done
        gate_blockers    gates it needs that are not yet cleared
        finish_blockers  strict "finish before finish" predecessors not yet done
        warnings         advisory predecessors not yet done (information only)

        Uses prefetched `dependencies__predecessor` and `gates__gate_type` when present.
        """
        start_blockers, finish_blockers, warnings = [], [], []
        for link in self.dependencies.all():
            if link.predecessor.is_done:
                continue
            if link.enforcement == Enforcement.ADVISORY:
                warnings.append(link.predecessor.name)
            elif link.kind == LinkKind.FINISH_TO_FINISH:
                finish_blockers.append(link.predecessor.name)
            else:
                start_blockers.append(link.predecessor.name)
        gate_blockers = [gate.gate_type.name for gate in self.gates.all() if not gate.is_cleared]
        return SimpleNamespace(
            start_blockers=start_blockers,
            gate_blockers=gate_blockers,
            finish_blockers=finish_blockers,
            warnings=warnings,
            can_start=not start_blockers and not gate_blockers,
            can_complete=not finish_blockers,
        )


class Dependency(models.Model):
    """`operation` comes after `predecessor`."""

    operation = models.ForeignKey(Operation, on_delete=models.CASCADE, related_name="dependencies")
    predecessor = models.ForeignKey(Operation, on_delete=models.CASCADE, related_name="dependents")
    kind = models.CharField("type", max_length=20, choices=LinkKind.choices, default=LinkKind.FINISH_TO_START)
    enforcement = models.CharField(max_length=10, choices=Enforcement.choices, default=Enforcement.STRICT)

    class Meta:
        ordering = ["predecessor__sequence", "id"]
        verbose_name_plural = "dependencies"
        constraints = [
            models.UniqueConstraint(fields=["operation", "predecessor"], name="unique_dependency"),
        ]

    def __str__(self):
        return f"{self.operation.name} after {self.predecessor.name}"
