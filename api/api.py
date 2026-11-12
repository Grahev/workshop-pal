"""Workshop Pal REST API, version 1.

Every endpoint calls the same service functions as the web screens, so the
rules (status only changes through events, history is never deleted) hold
for external systems too.
"""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from ninja import NinjaAPI

from floor.services import InvalidTransition
from routing.services import RouteError

from .auth import ApiKeyAuth
from .routers import board, files, floor, jobs, reference

DESCRIPTION = """
Create and update jobs, record shop-floor events, read the live board and attach files.

**Authentication:** send `Authorization: Bearer <api key>` with every request.
Keys are created in Setup → API keys. A key acts as its user, and that user's role
decides what the key can change.

**Errors** come back as `{"detail": "..."}`: 401 bad or missing key, 403 role not allowed,
404 not found, 409 not allowed in the current state, 422 invalid data.
"""

api = NinjaAPI(
    title="Workshop Pal API",
    version="1.0.0",
    description=DESCRIPTION,
    auth=ApiKeyAuth(),
    urls_namespace="api",
    docs_decorator=login_required,
)

api.add_router("", reference.router)
api.add_router("", jobs.router)
api.add_router("", floor.router)
api.add_router("", board.router)
api.add_router("", files.router)


@api.exception_handler(InvalidTransition)
def invalid_transition(request, exc):
    return api.create_response(request, {"detail": str(exc)}, status=409)


@api.exception_handler(RouteError)
def route_error(request, exc):
    return api.create_response(request, {"detail": str(exc)}, status=409)


@api.exception_handler(ValidationError)
def validation_error(request, exc):
    return api.create_response(request, {"detail": " ".join(exc.messages)}, status=422)
