"""Role checks shared by all apps."""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.utils.http import url_has_allowed_host_and_scheme

from .models import User

OFFICE_ROLES = {User.Role.OFFICE, User.Role.SUPERVISOR, User.Role.ADMIN}


def can_manage(user):
    """Office, supervisor and admin roles can create and change jobs, routes and files."""
    return user.is_authenticated and (user.is_superuser or user.role in OFFICE_ROLES)


def office_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not can_manage(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper


def safe_next(request, fallback):
    """The `next` URL from a form, if it points back at this site; otherwise the fallback."""
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    if url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return next_url
    return fallback
