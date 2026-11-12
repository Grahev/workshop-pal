from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from accounts.permissions import can_manage, office_required, safe_next
from board.selectors import build_row
from floor import services as floor_services
from floor.models import Event, Issue
from floor.services import InvalidTransition
from routing import services as routing_services
from routing.models import Dependency, Enforcement, Gate, GateType, LinkKind, Operation, RouteTemplate
from routing.services import RouteError

from . import services
from .forms import JobForm, JobItemForm, OperationForm
from .models import Job, JobItem


# ---------------------------------------------------------------- jobs

@login_required
def job_list(request):
    query = request.GET.get("q", "").strip()
    show = "archived" if request.GET.get("show") == "archived" else "active"
    done = Q(items__operations__status__in=Operation.DONE_STATUSES)
    jobs = (
        Job.objects.filter(is_archived=(show == "archived"))
        .select_related("customer")
        .annotate(
            item_count=Count("items", distinct=True),
            op_total=Count("items__operations", distinct=True),
            op_done=Count("items__operations", filter=done, distinct=True),
        )
    )
    if query:
        jobs = jobs.filter(
            Q(reference__icontains=query) | Q(title__icontains=query)
            | Q(customer__name__icontains=query) | Q(customer_reference__icontains=query)
        )
    return render(request, "jobs/job_list.html", {"jobs": jobs, "q": query, "show": show})


@office_required
def job_create(request):
    form = JobForm(request.POST or None, initial={"reference": services.suggest_reference()})
    if request.method == "POST" and form.is_valid():
        job = form.save(commit=False)
        job.created_by = request.user
        job.save()
        messages.success(request, "Job created. Now add the items to be made.")
        return redirect("jobs:detail", pk=job.pk)
    return render(request, "jobs/job_form.html", {"form": form, "job": None})


@office_required
def job_edit(request, pk):
    job = get_object_or_404(Job, pk=pk)
    form = JobForm(request.POST or None, instance=job)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("jobs:detail", pk=job.pk)
    return render(request, "jobs/job_form.html", {"form": form, "job": job})


@login_required
def job_detail(request, pk):
    job = get_object_or_404(Job.objects.select_related("customer", "site"), pk=pk)
    items = job.items.select_related("job", "product_type").prefetch_related(
        "operations__work_centre", "operations__hold_reason"
    )
    context = {
        "job": job,
        "rows": [build_row(item) for item in items],
        "events": Event.objects.filter(operation__item__job=job)
        .select_related("operation__item", "user", "hold_reason")
        .order_by("-occurred_at", "-id")[:100],
        "issues": Issue.objects.filter(operation__item__job=job).select_related(
            "operation__item", "raised_by", "resolved_by"
        ),
    }
    return render(request, "jobs/job_detail.html", context)


@office_required
@require_POST
def job_archive(request, pk):
    job = get_object_or_404(Job, pk=pk)
    job.is_archived = not job.is_archived
    job.save(update_fields=["is_archived"])
    messages.success(request, "Job archived." if job.is_archived else "Job restored.")
    return redirect("jobs:detail", pk=job.pk)


# ---------------------------------------------------------------- items

@office_required
def item_create(request, job_pk):
    job = get_object_or_404(Job, pk=job_pk)
    form = JobItemForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        item.job = job
        item.save()
        template = RouteTemplate.objects.filter(product_type=item.product_type, is_active=True).first()
        if item.product_type and template:
            routing_services.apply_template(item, template)
            messages.success(request, f"Item added with the '{template.name}' route. Fill in the spec and adjust the route.")
        else:
            messages.success(request, "Item added. Fill in the spec and build its route.")
        return redirect("jobs:item", pk=item.pk)
    return render(request, "jobs/item_form.html", {"form": form, "job": job})


@login_required
def item_detail(request, pk):
    item = get_object_or_404(JobItem.objects.select_related("job__customer", "product_type"), pk=pk)
    form = JobItemForm(instance=item)
    if request.method == "POST":
        if not can_manage(request.user):
            return redirect("jobs:item", pk=item.pk)
        form = JobItemForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            messages.success(request, "Item updated.")
            return redirect("jobs:item", pk=item.pk)

    operations = list(routing_services.route_operations(item).annotate(event_count=Count("events")))
    for op in operations:
        op.can_remove = op.status == Operation.Status.PENDING and op.event_count == 0
    context = {
        "item": item,
        "job": item.job,
        "form": form,
        "spec_fields": services.spec_form_fields(item),
        "operations": operations,
        "stages": routing_services.route_stages(operations),
        "gates": item.gates.select_related("gate_type", "cleared_by").prefetch_related("operations"),
        "gate_types": GateType.objects.filter(is_active=True),
        "enforcements": Enforcement.choices,
        "operation_form": OperationForm(),
        "templates": RouteTemplate.objects.filter(is_active=True),
        "issues": Issue.objects.filter(operation__item=item).select_related("operation__item", "raised_by", "resolved_by"),
        "row": build_row(item),
    }
    return render(request, "jobs/item_detail.html", context)


@office_required
@require_POST
def item_spec(request, pk):
    item = get_object_or_404(JobItem, pk=pk)
    spec, errors = services.spec_from_post(item, request.POST)
    if errors:
        for error in errors:
            messages.error(request, error)
    else:
        before = item.spec_version
        item = services.update_spec(item, spec, user=request.user)
        if item.spec_version != before:
            messages.success(request, f"Spec saved as version {item.spec_version}.")
    return redirect("jobs:item", pk=item.pk)


# ---------------------------------------------------------------- route editing

@office_required
@require_POST
def route_apply(request, pk):
    item = get_object_or_404(JobItem, pk=pk)
    template = get_object_or_404(RouteTemplate, pk=request.POST.get("template") or 0)
    if item.operations.exists():
        messages.error(request, "This item already has a route.")
    else:
        routing_services.apply_template(item, template)
    return redirect("jobs:item", pk=item.pk)


def _route_url(item_id):
    return reverse("jobs:item", args=[item_id]) + "#route"


def _choice(value, choices, default=None):
    return value if value in choices.values else default


@office_required
@require_POST
def operation_add(request, pk):
    item = get_object_or_404(JobItem, pk=pk)
    form = OperationForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Give the step a name and a work centre.")
        return redirect(_route_url(item.pk))
    try:
        routing_services.add_operation(
            item,
            after=item.operations.filter(pk__in=request.POST.getlist("after")),
            gate_types=GateType.objects.filter(pk__in=request.POST.getlist("needs")),
            **form.cleaned_data,
        )
    except RouteError as exc:
        messages.error(request, str(exc))
    return redirect(_route_url(item.pk))


@office_required
def operation_edit(request, pk):
    """Step editor: details, what the step comes after, and what it needs."""
    operation = get_object_or_404(Operation.objects.select_related("item__job", "work_centre"), pk=pk)
    form = OperationForm(request.POST or None, instance=operation)
    if request.method == "POST" and form.is_valid():
        try:
            # Reload so the status check is against the database, not the form's copy.
            routing_services.update_operation(Operation.objects.get(pk=pk), **form.cleaned_data)
            messages.success(request, "Step saved.")
        except RouteError as exc:
            messages.error(request, str(exc))
        return redirect("jobs:operation_edit", pk=pk)

    links = operation.dependencies.select_related("predecessor")
    linked = {link.predecessor_id for link in links}
    context = {
        "form": form,
        "operation": operation,
        "links": links,
        "candidates": operation.item.operations.exclude(pk=operation.pk).exclude(pk__in=linked).order_by("sequence", "id"),
        "kinds": LinkKind.choices,
        "enforcements": Enforcement.choices,
        "default_enforcement": operation.item.link_enforcement,
        "gate_types": GateType.objects.filter(is_active=True),
        "needed": set(operation.gates.values_list("gate_type_id", flat=True)),
    }
    return render(request, "jobs/operation_form.html", context)


@office_required
@require_POST
def link_add(request, pk):
    operation = get_object_or_404(Operation.objects.select_related("item"), pk=pk)
    predecessor = get_object_or_404(Operation, pk=request.POST.get("predecessor") or 0)
    try:
        routing_services.add_link(
            operation, predecessor,
            kind=_choice(request.POST.get("kind"), LinkKind, LinkKind.FINISH_TO_START),
            enforcement=_choice(request.POST.get("enforcement"), Enforcement),
        )
    except RouteError as exc:
        messages.error(request, str(exc))
    return redirect("jobs:operation_edit", pk=operation.pk)


@office_required
@require_POST
def link_update(request, pk):
    link = get_object_or_404(Dependency.objects.select_related("operation", "predecessor"), pk=pk)
    try:
        routing_services.update_link(
            link,
            kind=_choice(request.POST.get("kind"), LinkKind),
            enforcement=_choice(request.POST.get("enforcement"), Enforcement),
        )
    except RouteError as exc:
        messages.error(request, str(exc))
    return redirect("jobs:operation_edit", pk=link.operation_id)


@office_required
@require_POST
def link_remove(request, pk):
    link = get_object_or_404(Dependency, pk=pk)
    routing_services.remove_link(link)
    return redirect("jobs:operation_edit", pk=link.operation_id)


@office_required
@require_POST
def operation_gates(request, pk):
    operation = get_object_or_404(Operation.objects.select_related("item"), pk=pk)
    try:
        routing_services.set_required_gates(operation, GateType.objects.filter(pk__in=request.POST.getlist("needs")))
        messages.success(request, "Requirements saved.")
    except RouteError as exc:
        messages.error(request, str(exc))
    return redirect("jobs:operation_edit", pk=operation.pk)


@office_required
@require_POST
def route_sequence(request, pk):
    """Shortcut: re-link the whole route as a plain chain in its current order."""
    item = get_object_or_404(JobItem, pk=pk)
    try:
        routing_services.set_simple_sequence(item, _choice(request.POST.get("enforcement"), Enforcement))
        messages.success(request, "Route re-linked as a simple sequence.")
    except RouteError as exc:
        messages.error(request, str(exc))
    return redirect(_route_url(item.pk))


@office_required
@require_POST
def route_enforcement(request, pk):
    item = get_object_or_404(JobItem, pk=pk)
    try:
        routing_services.set_default_enforcement(item, request.POST.get("enforcement"))
    except RouteError as exc:
        messages.error(request, str(exc))
    return redirect(_route_url(item.pk))


@office_required
@require_POST
def route_save_template(request, pk):
    item = get_object_or_404(JobItem.objects.select_related("product_type"), pk=pk)
    try:
        template = routing_services.save_as_template(item, request.POST.get("name"), product_type=item.product_type)
        messages.success(request, f"Saved as template '{template.name}'.")
    except RouteError as exc:
        messages.error(request, str(exc))
    return redirect(_route_url(item.pk))


# ---------------------------------------------------------------- gates

@office_required
@require_POST
def gate_update(request, pk, action):
    """Clear a gate (the thing has arrived), reopen it, or set when it is expected."""
    gate = get_object_or_404(Gate.objects.select_related("gate_type"), pk=pk)
    note = request.POST.get("note", "").strip()
    if action == "clear":
        routing_services.clear_gate(gate, user=request.user, note=note)
    elif action == "reopen":
        routing_services.reopen_gate(gate, user=request.user, note=note)
    elif action == "expected":
        routing_services.set_gate_expected(gate, parse_date(request.POST.get("expected_date") or "") or None)
    return redirect(safe_next(request, reverse("jobs:item", args=[gate.item_id]) + "#gates"))


@office_required
@require_POST
def operation_move(request, pk, direction):
    operation = get_object_or_404(Operation, pk=pk)
    if direction in ("up", "down"):
        routing_services.move_operation(operation, direction)
    return redirect(reverse("jobs:item", args=[operation.item_id]) + "#route")


@office_required
@require_POST
def operation_remove(request, pk):
    operation = get_object_or_404(Operation, pk=pk)
    try:
        routing_services.remove_operation(operation)
    except RouteError as exc:
        messages.error(request, str(exc))
    return redirect(reverse("jobs:item", args=[operation.item_id]) + "#route")


@office_required
@require_POST
def operation_skip(request, pk):
    operation = get_object_or_404(Operation, pk=pk)
    try:
        floor_services.skip_operation(operation, user=request.user, note=request.POST.get("note", ""))
    except InvalidTransition as exc:
        messages.error(request, str(exc))
    return redirect(reverse("jobs:item", args=[operation.item_id]) + "#route")


# ---------------------------------------------------------------- issues

@office_required
@require_POST
def issue_resolve(request, pk):
    issue = get_object_or_404(Issue, pk=pk)
    floor_services.resolve_issue(issue, user=request.user, resolution=request.POST.get("resolution", "").strip())
    return redirect(safe_next(request, reverse("jobs:detail", args=[issue.operation.item.job_id])))
