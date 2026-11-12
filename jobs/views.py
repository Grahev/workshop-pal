from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.permissions import can_manage, office_required, safe_next
from board.selectors import build_row
from floor import services as floor_services
from floor.models import Event, Issue
from floor.services import InvalidTransition
from routing import services as routing_services
from routing.models import Operation, RouteTemplate
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
    form = JobForm(request.POST or None, initial={"reference": _suggest_reference()})
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


def _suggest_reference():
    """Next free J-number, as a starting point the user can overwrite."""
    number = Job.objects.count() + 1001
    while Job.objects.filter(reference=f"J-{number}").exists():
        number += 1
    return f"J-{number}"


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

    operations = list(
        item.operations.select_related("work_centre", "hold_reason")
        .annotate(event_count=Count("events"))
        .order_by("sequence", "id")
    )
    for op in operations:
        op.can_remove = op.status == Operation.Status.PENDING and op.event_count == 0
    context = {
        "item": item,
        "job": item.job,
        "form": form,
        "spec_fields": services.spec_form_fields(item),
        "operations": operations,
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


@office_required
@require_POST
def operation_add(request, pk):
    item = get_object_or_404(JobItem, pk=pk)
    form = OperationForm(request.POST)
    if form.is_valid():
        routing_services.add_operation(item, **form.cleaned_data)
    else:
        messages.error(request, "Give the step a name and a work centre.")
    return redirect(reverse("jobs:item", args=[item.pk]) + "#route")


@office_required
def operation_edit(request, pk):
    operation = get_object_or_404(Operation.objects.select_related("item__job"), pk=pk)
    form = OperationForm(request.POST or None, instance=operation)
    if request.method == "POST" and form.is_valid():
        try:
            # Reload so the status check is against the database, not the form's copy.
            routing_services.update_operation(Operation.objects.get(pk=pk), **form.cleaned_data)
        except RouteError as exc:
            messages.error(request, str(exc))
        return redirect(reverse("jobs:item", args=[operation.item_id]) + "#route")
    return render(request, "jobs/operation_form.html", {"form": form, "operation": operation})


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
