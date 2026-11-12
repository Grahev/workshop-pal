import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.permissions import can_manage
from attachments.services import attachments_for
from resources.models import WorkCentre
from routing.models import HoldReason, Operation

from . import selectors, services
from .models import Issue
from .services import InvalidTransition

# Barcodes on job sheets encode an operation as OP-<id>.
OPERATION_CODE = re.compile(r"^OP-?(\d+)$", re.IGNORECASE)

SIMPLE_ACTIONS = {
    "start": services.start_operation,
    "pause": services.pause_operation,
    "resume": services.resume_operation,
    "complete": services.complete_operation,
    "release": services.release_operation,
}


@login_required
def home(request):
    """Pick the work centre this device is standing at."""
    work_centres = WorkCentre.objects.filter(is_active=True, site__is_active=True).select_related("site")
    return render(request, "floor/home.html", {"work_centres": work_centres})


def _queue_context(work_centre, query="", error=""):
    return {
        "work_centre": work_centre,
        "q": query,
        "queue": selectors.station_queue(work_centre, query),
        "hold_reasons": HoldReason.objects.filter(is_active=True),
        "error": error,
    }


@login_required
def station(request, work_centre_id):
    """Workstation screen: the queue for one work centre, with scan and one-tap actions."""
    work_centre = get_object_or_404(WorkCentre, pk=work_centre_id)
    query = request.GET.get("q", "").strip()

    scanned = OPERATION_CODE.match(query)
    if scanned:
        operation = Operation.objects.filter(pk=scanned.group(1)).first()
        if operation:
            return redirect(f"{reverse('floor:operation', args=[operation.pk])}?station={work_centre.pk}")

    context = _queue_context(work_centre, query)
    template = "floor/station.html#queue" if request.headers.get("HX-Request") else "floor/station.html"
    return render(request, template, context)


@login_required
def operation_detail(request, pk):
    """Everything an operator needs for one operation: spec, drawings, route, history."""
    operation = get_object_or_404(
        Operation.objects.select_related("item__job__customer", "item__product_type", "work_centre", "hold_reason"),
        pk=pk,
    )
    item = operation.item
    work_centre = WorkCentre.objects.filter(pk=request.GET.get("station") or 0).first() or operation.work_centre
    context = {
        "operation": operation,
        "item": item,
        "job": item.job,
        "work_centre": work_centre,
        "spec_rows": selectors.spec_rows(item),
        "attachments": list(attachments_for(item)) + list(attachments_for(item.job)),
        "route": item.operations.select_related("work_centre").order_by("sequence"),
        "events": operation.events.select_related("user", "hold_reason").order_by("-occurred_at", "-id"),
        "waiting_on": list(operation.blocking_dependencies().values_list("name", flat=True)),
        "hold_reasons": HoldReason.objects.filter(is_active=True),
        "issues": operation.issues.select_related("operation__item", "raised_by", "resolved_by"),
        "issue_categories": Issue.Category.choices,
    }
    return render(request, "floor/operation.html", context)


@login_required
@require_POST
def operation_action(request, pk, action):
    """Record one shop-floor event. Used by the queue (HTMX) and the detail page (plain form)."""
    operation = get_object_or_404(Operation, pk=pk)
    work_centre = WorkCentre.objects.filter(pk=request.POST.get("station") or 0).first() or operation.work_centre
    note = request.POST.get("note", "").strip()
    error = ""

    try:
        if action in SIMPLE_ACTIONS:
            SIMPLE_ACTIONS[action](operation, user=request.user, work_centre=work_centre)
        elif action == "hold":
            reason = HoldReason.objects.filter(pk=request.POST.get("hold_reason") or 0).first()
            services.hold_operation(operation, reason, user=request.user, work_centre=work_centre, note=note)
        elif action == "rework":
            # Sending finished work back is a supervisor decision.
            if not can_manage(request.user):
                error = "Only a supervisor can send work back for rework."
            else:
                services.rework_operation(operation, user=request.user, work_centre=work_centre, note=note)
        elif action == "note":
            if note:
                services.add_note(operation, note, user=request.user, work_centre=work_centre)
        else:
            error = "Unknown action."
    except InvalidTransition as exc:
        error = str(exc)

    if request.headers.get("HX-Request"):
        context = _queue_context(work_centre, request.POST.get("q", "").strip(), error)
        return render(request, "floor/station.html#queue", context)

    if error:
        messages.error(request, error)
    next_url = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = reverse("floor:station", args=[work_centre.pk])
    return redirect(next_url)


@login_required
@require_POST
def raise_issue(request, pk):
    """Raise an issue or NCR against an operation, with an optional photo from the device camera."""
    operation = get_object_or_404(Operation, pk=pk)
    work_centre = WorkCentre.objects.filter(pk=request.POST.get("station") or 0).first() or operation.work_centre
    description = request.POST.get("description", "").strip()
    category = request.POST.get("category") or Issue.Category.ISSUE
    if not description:
        messages.error(request, "Describe the issue.")
    elif category not in Issue.Category.values:
        messages.error(request, "Unknown issue type.")
    else:
        try:
            services.raise_issue(
                operation, description, user=request.user, category=category,
                photo=request.FILES.get("photo"), work_centre=work_centre,
            )
            messages.success(request, "Issue raised.")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    next_url = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = f"{reverse('floor:operation', args=[operation.pk])}?station={work_centre.pk}"
    return redirect(next_url)
