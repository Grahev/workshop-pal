from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Custom user so roles and shop-floor sign-in can grow without a painful swap later."""

    class Role(models.TextChoices):
        OPERATOR = "operator", "Operator"
        SUPERVISOR = "supervisor", "Supervisor"
        OFFICE = "office", "Office / planner"
        ADMIN = "admin", "Admin"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.OPERATOR)

    def __str__(self):
        return self.get_full_name() or self.username
