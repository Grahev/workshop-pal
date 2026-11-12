"""Shop floor: operations, events, queues and issues."""

from typing import Optional

from django.shortcuts import get_object_or_404
from ninja import Query, Router
from ninja.errors import HttpError
from ninja.pagination import LimitOffsetPagination, paginate

from accounts.permissions import can_manage
from floor import selectors, services
from floor.models import Event, Issue
from resources.models import WorkCentre
from routing.models import Operation

from .. import lookups, queries, schemas
from ..auth import require_manage

router = Router(tags=["Shop floor"])


def _operations():
    return queries.operations()


def _events():
    return Event.objects.select_related("operation__item__job", "user", "hold_reason")


def _issues():
    return Issue.objects.select_related("operation__item__job", "raised_by", "resolved_by")


# ---------------------------------------------------------------- operations

@router.get("/operations", response=list[schemas.OperationOut], summary="List operations")
@paginate(LimitOffsetPagination)
def list_operations(
    request,
    status: Optional[str] = None,
    work_centre_id: Optional[int] = None,
    job_id: Optional[int] = None,
    item_id: Optional[int] = None,
):
    operations = _operations().filter(item__job__is_archived=False).order_by("item_id", "sequence", "id")
    if status:
        operations = operations.filter(status=status)
    if work_centre_id:
        operations = operations.filter(work_centre_id=work_centre_id)
    if job_id:
        operations = operations.filter(item__job_id=job_id)
    if item_id:
        operations = operations.filter(item_id=item_id)
    return operations


@router.get("/operations/{operation_id}", response=schemas.OperationOut, summary="Get an operation")
def get_operation(request, operation_id: int):
    return get_object_or_404(_operations(), pk=operation_id)


@router.get("/work-centres/{work_centre_id}/queue", response=schemas.QueueOut, summary="A work centre's queue")
def work_centre_queue(request, work_centre_id: int, q: str = ""):
    """The same grouping the workstation screen shows: active, on hold, ready and upcoming."""
    work_centre = get_object_or_404(WorkCentre, pk=work_centre_id)
    return selectors.station_queue(work_centre, q)


# ---------------------------------------------------------------- events

@router.post("/operations/{operation_id}/events", response={201: schemas.EventResult}, summary="Record a shop-floor event")
def record_event(request, operation_id: int, payload: schemas.EventIn):
    """This is the only way an operation's status changes.

    Allowed moves: `start` (pending, and nothing strict or gated outstanding), `pause` (in progress), `resume` (paused),
    `complete` (in progress or paused), `hold` (needs a hold reason), `release`,
    `rework` (completed; office roles only), `skip` (pending; office roles only), `note`.
    A move that is not allowed returns 409 and records nothing.
    """
    operation = get_object_or_404(Operation, pk=operation_id)
    if payload.type in ("rework", "skip"):
        require_manage(request)
    reason = lookups.hold_reason(payload.hold_reason_id, payload.hold_reason)
    if payload.type == "note" and not payload.note.strip():
        raise HttpError(422, "A note event needs a note.")
    event = services.record_event(
        operation,
        payload.type,
        user=request.user,
        work_centre=lookups.work_centre(payload.work_centre_id, required=False) or operation.work_centre,
        note=payload.note,
        hold_reason=reason,
        occurred_at=payload.occurred_at,
    )
    return 201, {"event": _events().get(pk=event.pk), "operation": _operations().get(pk=operation_id)}


@router.get("/operations/{operation_id}/events", response=list[schemas.EventOut], summary="An operation's history")
def operation_events(request, operation_id: int):
    get_object_or_404(Operation, pk=operation_id)
    return _events().filter(operation_id=operation_id).order_by("id")


@router.get("/events", response=list[schemas.EventOut], summary="Event feed")
def event_feed(
    request,
    after_id: int = 0,
    job_id: Optional[int] = None,
    type: Optional[schemas.EventType] = None,
    limit: int = Query(100, ge=1, le=500),
):
    """Everything that has happened, oldest first. To stay in sync, remember the last `id`
    you received and pass it as `after_id` on the next call."""
    events = _events().filter(pk__gt=after_id).order_by("id")
    if job_id:
        events = events.filter(operation__item__job_id=job_id)
    if type:
        events = events.filter(type=type)
    return events[:limit]


# ---------------------------------------------------------------- issues

@router.get("/issues", response=list[schemas.IssueOut], summary="List issues and NCRs")
@paginate(LimitOffsetPagination)
def list_issues(request, open: Optional[bool] = None, job_id: Optional[int] = None, category: Optional[str] = None):
    issues = _issues().order_by("-id")
    if open is not None:
        issues = issues.filter(resolved_at__isnull=open)
    if job_id:
        issues = issues.filter(operation__item__job_id=job_id)
    if category:
        issues = issues.filter(category=category)
    return issues


@router.post("/operations/{operation_id}/issues", response={201: schemas.IssueOut}, summary="Raise an issue or NCR")
def raise_issue(request, operation_id: int, payload: schemas.IssueIn):
    """To add a photo, upload it to `/attachments` with `target_type=issue` and the returned id."""
    operation = get_object_or_404(Operation, pk=operation_id)
    issue = services.raise_issue(
        operation,
        payload.description,
        user=request.user,
        category=payload.category,
        work_centre=lookups.work_centre(payload.work_centre_id, required=False) or operation.work_centre,
    )
    return 201, _issues().get(pk=issue.pk)


@router.post("/issues/{issue_id}/resolve", response=schemas.IssueOut, summary="Resolve an issue")
def resolve_issue(request, issue_id: int, payload: schemas.ResolveIn):
    if not can_manage(request.user):
        raise HttpError(403, "Only office, supervisor or admin roles can resolve issues.")
    issue = get_object_or_404(Issue, pk=issue_id)
    services.resolve_issue(issue, user=request.user, resolution=payload.resolution.strip())
    return _issues().get(pk=issue_id)
