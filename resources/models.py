from django.db import models


class Site(models.Model):
    """A physical workshop. One for now; present so multi-site needs no restructuring."""

    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class WorkCentre(models.Model):
    """A place or machine group where operations happen. Has a queue of operations."""

    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="work_centres")
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20, help_text="Short code shown on boards and job sheets")
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["site", "display_order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["site", "code"], name="unique_work_centre_code_per_site"),
        ]

    def __str__(self):
        return self.name
