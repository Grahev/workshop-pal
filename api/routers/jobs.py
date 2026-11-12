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
from routing.models import Dependency, Gate, Operation

from .. import lookups, queries, schemas
from ..auth import require_manage

router = Router(tags=["Jobs"])


def _step(payload: schemas.OperationIn):
    """An OperationIn as the step dict routing.services.add_steps takes."""
    step = {
        "name": payload.name,
        "work_centre": lookups.work_centre(payload.work_centre_id, payload.work_centre),
        "estimated_minutes": payload.estimated_minutes,
        "gate_types": [lookups.gate_type(name=name) for name in payload.needs],
    }
    if payload.after is not None:
        step["after"] = [
            (ref.step, ref.type, ref.enforcement) if isinstance(ref, schemas.AfterIn) else (ref, None, None)
            for ref in payload.after
        ]
    return step


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
        operations=[_step(op) for op in payload.operations] if payload.operations else None,
        default_route=payload.apply_default_route,
        link_enforcement=payload.link_enforcement,
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
    if changes.get("link_enforcement"):
        item.link_enforcement = changes["link_enforcement"]
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
    return get_object_or_404(queries.operations(), pk=pk)


def _route(item_id):
    return queries.operations().filter(item_id=item_id).order_by("sequence", "id")


@router.post("/items/{item_id}/route/apply-template", response=schemas.ItemOut, summary="Give an item its route from a template")
def apply_template(request, item_id: int, template_id: int):
    require_manage(request)
    item = get_object_or_404(JobItem.objects.select_related("job", "product_type"), pk=item_id)
    if item.operations.exists():
        raise HttpError(409, "This item already has a route.")
    routing_services.apply_template(item, lookups.route_template(template_id))
    return item


@router.post("/items/{item_id}/operations", response={201: schemas.OperationOut}, summary="Add a step to an item's route")
def add_operation(request, item_id: int, payload: schemas.OperationIn):
    """Without `after`, the step follows the route's current last step. `after: []` makes it free to start."""
    require_manage(request)
    item = get_object_or_404(JobItem, pk=item_id)
    operation = routing_services.add_steps(item, [_step(payload)])[0]
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
    return _route(operation.item_id)


@router.delete("/operations/{operation_id}", response={204: None}, summary="Remove a step that has no history")
def remove_operation(request, operation_id: int):
    """Steps with history cannot be removed. Record a `skip` event instead."""
    require_manage(request)
    routing_services.remove_operation(_operation(operation_id))
    return 204, None


# ---------------------------------------------------------------- links

@router.post("/items/{item_id}/route/sequence", response=list[schemas.OperationOut], summary="Re-link a route as a simple sequence")
def set_sequence(request, item_id: int, payload: schemas.SequenceIn):
    """Replaces every link with a plain chain in the current display order."""
    require_manage(request)
    item = get_object_or_404(JobItem, pk=item_id)
    routing_services.set_simple_sequence(item, payload.enforcement)
    return _route(item_id)


@router.post("/items/{item_id}/route/save-template", response={201: schemas.RouteTemplateOut}, summary="Save an item's route as a template")
def save_template(request, item_id: int, payload: schemas.SaveTemplateIn):
    require_manage(request)
    item = get_object_or_404(JobItem.objects.select_related("product_type"), pk=item_id)
    return 201, routing_services.save_as_template(item, payload.name, product_type=item.product_type)


@router.post("/operations/{operation_id}/links", response=schemas.OperationOut, summary="Make a step come after another")
def add_link(request, operation_id: int, payload: schemas.LinkIn):
    """Creates the link, or updates it if the two steps are already linked.

    `finish_to_start`: the earlier step must finish before this one starts.
    `finish_to_finish`: this one can start, but cannot be completed until the earlier step finishes.
    `strict` blocks; `advisory` only warns. Loops are refused, and a started step cannot be made stricter.
    """
    require_manage(request)
    operation = get_object_or_404(Operation.objects.select_related("item"), pk=operation_id)
    predecessor = get_object_or_404(Operation, pk=payload.after)
    routing_services.add_link(operation, predecessor, kind=payload.type, enforcement=payload.enforcement)
    return _operation(operation_id)


def _link(operation_id, after_id):
    return get_object_or_404(
        Dependency.objects.select_related("operation", "predecessor"), operation_id=operation_id, predecessor_id=after_id
    )


@router.patch("/operations/{operation_id}/links/{after_id}", response=schemas.OperationOut, summary="Change a link's type or enforcement")
def update_link(request, operation_id: int, after_id: int, payload: schemas.LinkPatch):
    require_manage(request)
    routing_services.update_link(_link(operation_id, after_id), kind=payload.type, enforcement=payload.enforcement)
    return _operation(operation_id)


@router.delete("/operations/{operation_id}/links/{after_id}", response=schemas.OperationOut, summary="Remove a link")
def remove_link(request, operation_id: int, after_id: int):
    require_manage(request)
    routing_services.remove_link(_link(operation_id, after_id))
    return _operation(operation_id)


# ---------------------------------------------------------------- gates

@router.get("/gates", response=list[schemas.GateOut], summary="List gates")
def list_gates(request, cleared: Optional[bool] = None, job_id: Optional[int] = None, item_id: Optional[int] = None,
               gate_type: Optional[str] = None):
    """For example `?cleared=false&gate_type=Hardware` lists everything still waiting on hardware."""
    gates = queries.gates().filter(item__job__is_archived=False).order_by("id")
    if cleared is not None:
        gates = gates.filter(is_cleared=cleared)
    if job_id:
        gates = gates.filter(item__job_id=job_id)
    if item_id:
        gates = gates.filter(item_id=item_id)
    if gate_type:
        gates = gates.filter(gate_type__name__iexact=gate_type)
    return gates


@router.post("/operations/{operation_id}/gates", response=schemas.OperationOut, summary="Make a step need a gate")
def require_gate(request, operation_id: int, payload: schemas.GateIn):
    """The step cannot start until the gate is cleared. The gate is created on the item if needed."""
    require_manage(request)
    operation = get_object_or_404(Operation.objects.select_related("item"), pk=operation_id)
    routing_services.require_gate(operation, lookups.gate_type(payload.gate_type_id, payload.gate_type))
    return _operation(operation_id)


@router.delete("/operations/{operation_id}/gates/{gate_id}", response=schemas.OperationOut, summary="Stop a step needing a gate")
def drop_gate(request, operation_id: int, gate_id: int):
    require_manage(request)
    operation = get_object_or_404(Operation, pk=operation_id)
    routing_services.drop_gate_requirement(operation, get_object_or_404(Gate, pk=gate_id, item_id=operation.item_id))
    return _operation(operation_id)


@router.post("/gates/{gate_id}/clear", response=schemas.GateOut, summary="Mark a gate as available")
def clear_gate(request, gate_id: int, payload: schemas.GateNoteIn):
    """For example when hardware arrives. Steps waiting on it can then start."""
    require_manage(request)
    routing_services.clear_gate(get_object_or_404(Gate, pk=gate_id), user=request.user, note=payload.note.strip())
    return queries.gates().get(pk=gate_id)


@router.post("/gates/{gate_id}/reopen", response=schemas.GateOut, summary="Mark a gate as waiting again")
def reopen_gate(request, gate_id: int, payload: schemas.GateNoteIn):
    require_manage(request)
    routing_services.reopen_gate(get_object_or_404(Gate, pk=gate_id), user=request.user, note=payload.note.strip())
    return queries.gates().get(pk=gate_id)


@router.patch("/gates/{gate_id}", response=schemas.GateOut, summary="Set when a gate is expected to clear")
def update_gate(request, gate_id: int, payload: schemas.GatePatch):
    require_manage(request)
    routing_services.set_gate_expected(get_object_or_404(Gate, pk=gate_id), payload.expected_date)
    return queries.gates().get(pk=gate_id)
