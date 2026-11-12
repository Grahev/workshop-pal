"""Live board: the same data the board screens show."""

from typing import Literal, Optional

from ninja import Router

from board import selectors

from .. import schemas

router = Router(tags=["Live board"])

BoardStatus = Literal["", "attention", "in_progress", "paused", "on_hold", "pending", "late", "no_route", "completed"]


def _row(row):
    return {
        "item_id": row.item.pk,
        "item_name": row.item.name,
        "job_id": row.job.pk,
        "job_reference": row.job.reference,
        "customer": row.job.customer.name,
        "due_date": row.job.due_date,
        "priority": schemas.PRIORITY_NAME.get(row.job.priority, str(row.job.priority)),
        "state": row.state,
        "current_operation": row.current,
        "steps_done": row.done,
        "steps_total": row.total,
        "percent": row.percent,
        "is_late": row.is_late,
        "at_risk": row.at_risk,
        "held_since": row.held_since,
    }


@router.get("/board/items", response=schemas.BoardOut, summary="Where every active item is")
def board_items(
    request,
    q: str = "",
    customer_id: Optional[int] = None,
    work_centre_id: Optional[int] = None,
    status: BoardStatus = "",
):
    """One row per item: current step, state, progress, and late / at-risk flags.

    Completed items are left out unless `status=completed`. `attention` returns
    items that are late, at risk or on hold.
    """
    rows, summary = selectors.board_rows(q, customer_id, work_centre_id, status)
    return {"summary": summary, "items": [_row(row) for row in rows]}


@router.get("/board/work-centres", response=list[schemas.LaneOut], summary="Open work grouped by work centre")
def board_work_centres(request, q: str = "", customer_id: Optional[int] = None, work_centre_id: Optional[int] = None):
    return selectors.board_lanes(q, customer_id, work_centre_id)
