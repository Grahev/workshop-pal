"""Shop-floor business logic.

This is the only place operation status changes. Every change writes an Event
first, then updates the cached projection on Operation in the same transaction.
Views, admin and the API all call these functions.
"""

from django.db import transaction
from django.utils import timezone

from routing.models import Operation

from .models import Event

Status = Operation.Status
Type = Event.Type


class InvalidTransition(Exception):
    """The requested event is not allowed for the operation's current state."""


# event type -> (statuses it is allowed from, resulting status)
# RELEASE has no fixed result: it returns to where the operation was held from.
TRANSITIONS = {
    Type.START: ({Status.PENDING}, Status.IN_PROGRESS),
    Type.PAUSE: ({Status.IN_PROGRESS}, Status.PAUSED),
    Type.RESUME: ({Status.PAUSED}, Status.IN_PROGRESS),
    Type.COMPLETE: ({Status.IN_PROGRESS, Status.PAUSED}, Status.COMPLETED),
    Type.HOLD: ({Status.PENDING, Status.IN_PROGRESS, Status.PAUSED}, Status.ON_HOLD),
    Type.RELEASE: ({Status.ON_HOLD}, None),
    Type.REWORK: ({Status.COMPLETED}, Status.PENDING),
    Type.SKIP: ({Status.PENDING}, Status.SKIPPED),
}


@transaction.atomic
def record_event(operation, event_type, user=None, work_centre=None, note="", hold_reason=None, occurred_at=None):
    """Write an event and bring the operation's cached status in line with it."""
    operation = Operation.objects.select_for_update().get(pk=operation.pk)
    occurred_at = occurred_at or timezone.now()

    if event_type != Type.NOTE:
        if event_type not in TRANSITIONS:
            raise InvalidTransition(f"Unknown event type: {event_type}")
        allowed_from, new_status = TRANSITIONS[event_type]
        if operation.status not in allowed_from:
            raise InvalidTransition(
                f"Cannot {event_type} an operation that is {operation.get_status_display().lower()}."
            )
        if event_type == Type.START:
            blocking = list(operation.blocking_dependencies().values_list("name", flat=True))
            if blocking:
                raise InvalidTransition(f"Waiting on: {', '.join(blocking)}.")
        if event_type == Type.HOLD and hold_reason is None:
            raise InvalidTransition("A hold needs a reason.")

    event = Event.objects.create(
        operation=operation,
        type=event_type,
        user=user,
        work_centre=work_centre,
        hold_reason=hold_reason if event_type == Type.HOLD else None,
        note=note,
        occurred_at=occurred_at,
    )

    if event_type == Type.NOTE:
        return event

    if event_type == Type.START:
        operation.started_at = operation.started_at or occurred_at
    elif event_type == Type.COMPLETE:
        operation.completed_at = occurred_at
    elif event_type == Type.HOLD:
        operation.held_from = operation.status
        operation.hold_reason = hold_reason
    elif event_type == Type.RELEASE:
        # Work that was running when held comes back paused, so someone
        # deliberately resumes it and the time on hold is not counted as work.
        new_status = Status.PENDING if operation.held_from == Status.PENDING else Status.PAUSED
        operation.held_from = ""
        operation.hold_reason = None
    elif event_type == Type.REWORK:
        operation.completed_at = None
        operation.rework_count += 1

    operation.status = new_status
    operation.status_changed_at = occurred_at
    operation.save()
    return event


def start_operation(operation, user=None, work_centre=None):
    return record_event(operation, Type.START, user=user, work_centre=work_centre)


def pause_operation(operation, user=None, work_centre=None, note=""):
    return record_event(operation, Type.PAUSE, user=user, work_centre=work_centre, note=note)


def resume_operation(operation, user=None, work_centre=None):
    return record_event(operation, Type.RESUME, user=user, work_centre=work_centre)


def complete_operation(operation, user=None, work_centre=None, note=""):
    return record_event(operation, Type.COMPLETE, user=user, work_centre=work_centre, note=note)


def hold_operation(operation, hold_reason, user=None, work_centre=None, note=""):
    return record_event(
        operation, Type.HOLD, user=user, work_centre=work_centre, note=note, hold_reason=hold_reason
    )


def release_operation(operation, user=None, work_centre=None, note=""):
    return record_event(operation, Type.RELEASE, user=user, work_centre=work_centre, note=note)


def rework_operation(operation, user=None, work_centre=None, note=""):
    return record_event(operation, Type.REWORK, user=user, work_centre=work_centre, note=note)


def skip_operation(operation, user=None, note=""):
    return record_event(operation, Type.SKIP, user=user, note=note)


def add_note(operation, note, user=None, work_centre=None):
    return record_event(operation, Type.NOTE, user=user, work_centre=work_centre, note=note)
