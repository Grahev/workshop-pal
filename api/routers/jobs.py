"""Jobs, items, specs and routes."""

from datetime import date
from typing import Optional

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from ninja import Router
from ninja.errors import HttpError
from ninja.pagination import LimitOffsetPagination, paginate

from jobs import services as job_services
from jobs.models import Job, JobItem
from routing import services as routing_services
from routing.models import Operation

from .. import lookups, schemas
from ..auth import require_manage

router = Router(tags=["Jobs"])


def _operation_kwargs(payload):
    return {
        "name": payload.name,
        "work_centre": lookups.work_centre(payload.work_centre_id, payload.work_centre),
        "estimated_minutes": payload.estimated_minutes,
    }


def _create_item(job, payload: schemas.ItemIn):
    product_type = lookups.product_type(payload.product_type_id, payload.product_type)
    errors = job_services.validate_spec(product_type, payload.spec)
    if errors:
        raise HttpError(422, f"Item '{payload.name}': " + " ".join(errors))
    return job_services.create_item(
        job,
        name=payload.name,
        product_type=product_type,
        quantity=payload.quantity,
        spec=payload.spec,
        route_template=lookups.route_template(payload.route_template_id) if payload.route_template_id else None,
        operations=[_operation_kwargs(op) for op in payload.operations] if payload.operations else None,
        default_route=payload.apply_default_route,
    )


# ---------------------------------------------------------------- jobs

@router.get("/jobs", response=list[schemas.JobOut], summary="List jobs")
@paginate(LimitOffsetPagination)
def list_jobs(
    request,
    q: str = "",
    reference: Optional[str] = None,
    customer_id: Optional[int] = None,
    archived: bool = False,
    due_before: Optional[date] = None,
    due_after: Optional[date] = None,
):
    """Active jobs by default. Use `reference` for an exact match on the job number."""
    jobs = Job.objects.filter(is_archived=archived).select_related("customer").order_by("id")
    if reference:
        jobs = jobs.filter(reference=reference)
    if q:
        jobs = jobs.filter(
            Q(reference__icontains=q) | Q(title__icontains=q)
            | Q(customer__name__icontains=q) | Q(customer_reference__icontains=q)
        )
    if customer_id:
        jobs = jobs.filter(customer_id=customer_id)
    if due_before:
        jobs = jobs.filter(due_date__lte=due_before)
    if due_after:
        jobs = jobs.filter(due_date__gte=due_after)
    return jobs


@router.post("/jobs", response={201: schemas.JobDetailOut}, summary="Create a job, optionally with its items and routes")
def create_job(request, payload: schemas.JobIn):
    """Creates the job and everything under it in one transaction: if any item is invalid, nothing is saved.

    Each item's route comes from, in order: its `operations` list, its `route_template_id`,
    or the product type's template.
    """
    require_manage(request)
    reference = (payload.reference or "").strip() or job_services.suggest_reference()
    if Job.objects.filter(reference=reference).exists():
        raise HttpError(409, f"A job with reference {reference} already exists.")
    with transaction.atomic():
        job = Job.objects.create(
            reference=reference,
            title=payload.title,
            customer=lookups.customer(payload.customer_id, payload.customer),
            customer_reference=payload.customer_reference,
            site=lookups.site(payload.site_id),
            due_date=payload.due_date,
            priority=schemas.PRIORITY_VALUE[payload.priority],
            notes=payload.notes,
            created_by=request.user,
        )
        for item in payload.items:
            _create_item(job, item)
    return 201, job


@router.get("/jobs/{job_id}", response=schemas.JobDetailOut, summary="Get a job with its items and routes")
def get_job(request, job_id: int):
    return get_object_or_404(Job.objects.select_related("customer"), pk=job_id)


@router.patch("/jobs/{job_id}", response=schemas.JobDetailOut, summary="Update a job")
def update_job(request, job_id: int, payload: schemas.JobPatch):
    """Send only the fields to change. `due_date: null` clears the due date."""
    require_manage(request)
    job = get_object_or_404(Job, pk=job_id)
    changes = payload.dict(exclude_unset=True)

    if changes.get("reference") and changes["reference"] != job.reference:
        if Job.objects.filter(reference=changes["reference"]).exists():
            raise HttpError(409, f"A job with reference {changes['reference']} already exists.")
        job.reference = changes["reference"]
    if changes.get("customer_id") or changes.get("customer"):
        job.customer = lookups.customer(changes.get("customer_id"), changes.get("customer"))
    if changes.get("site_id"):
        job.site = lookups.site(changes["site_id"])
    if changes.get("priority"):
        job.priority = schemas.PRIORITY_VALUE[changes["priority"]]
    if "due_date" in changes:
        job.due_date = changes["due_date"]
    for field in ("title", "customer_reference", "notes"):
        if changes.get(field) is not None:
            setattr(job, field, changes[field])
    job.save()
    return job


@router.post("/jobs/{job_id}/archive", response=schemas.JobOut, summary="Archive a job")
def archive_job(request, job_id: int):
    require_manage(request)
    job = get_object_or_404(Job.objects.select_related("customer"), pk=job_id)
    job.is_archived = True
    job.save(update_fields=["is_archived"])
    return job


@router.post("/jobs/{job_id}/restore", response=schemas.JobOut, summary="Restore an archived job")
def restore_job(request, job_id: int):
    require_manage(request)
    job = get_object_or_404(Job.objects.select_related("customer"), pk=job_id)
    job.is_archived = False
    job.save(update_fields=["is_archived"])
    return job


# ---------------------------------------------------------------- items

@router.post("/jobs/{job_id}/items", response={201: schemas.ItemOut}, summary="Add an item to a job")
def create_item(request, job_id: int, payload: schemas.ItemIn):
    require_manage(request)
    job = get_object_or_404(Job, pk=job_id)
    with transaction.atomic():
        item = _create_item(job, payload)
    return 201, item


@router.get("/items/{item_id}", response=schemas.ItemOut, summary="Get an item with its spec and route")
def get_item(request, item_id: int):
    return get_object_or_404(JobItem.objects.select_related("job", "product_type"), pk=item_id)


@router.patch("/items/{item_id}", response=schemas.ItemOut, summary="Update an item's name, quantity or product type")
def update_item(request, item_id: int, payload: schemas.ItemPatch):
    require_manage(request)
    item = get_object_or_404(JobItem.objects.select_related("job", "product_type"), pk=item_id)
    changes = payload.dict(exclude_unset=True)
    if changes.get("name"):
        item.name = changes["name"]
    if changes.get("quantity"):
        item.quantity = changes["quantity"]
    if changes.get("product_type_id") or changes.get("product_type"):
        item.product_type = lookups.product_type(changes.get("product_type_id"), changes.get("product_type"))
    item.save()
    return item


@router.put("/items/{item_id}/spec", response=schemas.ItemOut, summary="Replace an item's spec")
def replace_spec(request, item_id: int, payload: schemas.SpecIn):
    """Replaces the whole spec. The previous version is kept in the spec history."""
    require_manage(request)
    item = get_object_or_404(JobItem.objects.select_related("job", "product_type"), pk=item_id)
    errors = job_services.validate_spec(item.product_type, payload.spec)
    if errors:
        raise HttpError(422, " ".join(errors))
    job_services.update_spec(item, payload.spec, user=request.user, note=payload.note)
    item.refresh_from_db()
    return item


@router.get("/items/{item_id}/spec-history", response=list[schemas.SpecRevisionOut], summary="Earlier versions of an item's spec")
def spec_history(request, item_id: int):
    item = get_object_or_404(JobItem, pk=item_id)
    return item.spec_revisions.select_related("changed_by")


# ---------------------------------------------------------------- routes

def _operation(pk):
    return get_object_or_404(Operation.objects.select_related("work_centre", "hold_reason", "item__job"), pk=pk)


@router.post("/items/{item_id}/route/apply-template", response=schemas.ItemOut, summary="Give an item its route from a template")
def apply_template(request, item_id: int, template_id: int):
    require_manage(request)
    item = get_object_or_404(JobItem.objects.select_related("job", "product_type"), pk=item_id)
    if item.operations.exists():
        raise HttpError(409, "This item already has a route.")
    routing_services.apply_template(item, lookups.route_template(template_id))
    return item


@router.post("/items/{item_id}/operations", response={201: schemas.OperationOut}, summary="Add a step to the end of an item's route")
def add_operation(request, item_id: int, payload: schemas.OperationIn):
    require_manage(request)
    item = get_object_or_404(JobItem, pk=item_id)
    operation = routing_services.add_operation(item, **_operation_kwargs(payload))
    return 201, _operation(operation.pk)


@router.patch("/operations/{operation_id}", response=schemas.OperationOut, summary="Change a step that has not started")
def update_operation(request, operation_id: int, payload: schemas.OperationPatch):
    require_manage(request)
    operation = _operation(operation_id)
    changes = payload.dict(exclude_unset=True)
    work_centre = operation.work_centre
    if changes.get("work_centre_id") or changes.get("work_centre"):
        work_centre = lookups.work_centre(changes.get("work_centre_id"), changes.get("work_centre"))
    routing_services.update_operation(
        operation,
        name=changes.get("name") or operation.name,
        work_centre=work_centre,
        estimated_minutes=operation.estimated_minutes if changes.get("estimated_minutes") is None else changes["estimated_minutes"],
    )
    return _operation(operation_id)


@router.post("/operations/{operation_id}/move", response=list[schemas.OperationOut], summary="Move a step up or down the route")
def move_operation(request, operation_id: int, payload: schemas.MoveIn):
    """Returns the item's whole route in its new order."""
    require_manage(request)
    operation = _operation(operation_id)
    routing_services.move_operation(operation, payload.direction)
    return operation.item.operations.select_related("work_centre", "hold_reason", "item__job").order_by("sequence", "id")


@router.delete("/operations/{operation_id}", response={204: None}, summary="Remove a step that has no history")
def remove_operation(request, operation_id: int):
    """Steps with history cannot be removed. Record a `skip` event instead."""
    require_manage(request)
    routing_services.remove_operation(_operation(operation_id))
    return 204, None
