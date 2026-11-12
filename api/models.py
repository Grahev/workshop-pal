import hashlib
import secrets

from django.conf import settings
from django.db import models


class ApiKey(models.Model):
    """A secret an external system uses to call the API.

    The key acts as its user: the user's role decides what the key may do,
    and events and changes made through the API are recorded against that user.
    Only a hash is stored, so the key itself is shown once, when it is created.
    """

    name = models.CharField(max_length=100, help_text="Which system uses this key, e.g. 'ERP sync'")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_keys",
        help_text="The key acts as this user. Their role decides what it can change.",
    )
    prefix = models.CharField(max_length=12, editable=False)
    key_hash = models.CharField(max_length=64, unique=True, editable=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["name"]
        verbose_name = "API key"

    def __str__(self):
        return f"{self.name} ({self.prefix}…)"

    @staticmethod
    def hash(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode()).hexdigest()

    def set_new_key(self) -> str:
        """Generate a fresh secret, store its hash, and return the secret to show once."""
        raw_key = "wp_" + secrets.token_urlsafe(32)
        self.prefix = raw_key[:10]
        self.key_hash = self.hash(raw_key)
        return raw_key

    @classmethod
    def issue(cls, name, user):
        key = cls(name=name, user=user)
        raw_key = key.set_new_key()
        key.save()
        return key, raw_key
