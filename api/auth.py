from datetime import timedelta

from django.utils import timezone
from ninja.errors import HttpError
from ninja.security import HttpBearer

from accounts.permissions import can_manage

from .models import ApiKey


class ApiKeyAuth(HttpBearer):
    """Authorization: Bearer <api key>. The request then runs as the key's user."""

    def authenticate(self, request, token):
        key = (
            ApiKey.objects.select_related("user")
            .filter(key_hash=ApiKey.hash(token), is_active=True, user__is_active=True)
            .first()
        )
        if key is None:
            return None
        request.user = key.user
        now = timezone.now()
        # Avoid a database write on every request.
        if key.last_used_at is None or now - key.last_used_at > timedelta(minutes=1):
            ApiKey.objects.filter(pk=key.pk).update(last_used_at=now)
        return key


def require_manage(request):
    """Changing jobs, specs, routes and files needs an office, supervisor or admin role."""
    if not can_manage(request.user):
        raise HttpError(403, "This API key's user does not have a role that can make this change.")
