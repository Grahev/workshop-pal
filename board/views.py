from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from jobs.models import Customer
from resources.models import WorkCentre

from . import selectors

STATUS_CHOICES = [
    ("", "All active"),
    ("attention", "Needs attention"),
    ("in_progress", "In progress"),
    ("paused", "Paused"),
    ("on_hold", "On hold"),
    ("pending", "Waiting"),
    ("late", "Late"),
    ("gate", "Waiting on a gate"),
    ("no_route", "No route"),
    ("completed", "Completed"),
]


def _int(value):
    return int(value) if value and value.isdigit() else None


@login_required
def board(request):
    """Live board: every active item by job, or every open operation by work centre."""
    view = "centres" if request.GET.get("view") == "centres" else "items"
    filters = {
        "q": request.GET.get("q", "").strip(),
        "customer": _int(request.GET.get("customer")),
        "work_centre": _int(request.GET.get("work_centre")),
        "status": request.GET.get("status", ""),
    }

    rows, summary = selectors.board_rows(
        filters["q"], filters["customer"], filters["work_centre"], filters["status"]
    )
    context = {
        "view": view,
        "f": filters,
        "filtered": any(filters.values()),
        "rows": rows,
        "summary": summary,
        "now": timezone.localtime(),
    }
    if view == "centres":
        context["lanes"] = selectors.board_lanes(filters["q"], filters["customer"], filters["work_centre"])

    if request.headers.get("HX-Request"):
        return render(request, "board/board.html#live", context)

    context.update(
        customers=Customer.objects.filter(jobs__is_archived=False).distinct(),
        work_centres=WorkCentre.objects.filter(is_active=True),
        status_choices=STATUS_CHOICES,
    )
    return render(request, "board/board.html", context)
