"""Reference data: the things jobs and routes point at. Read-only apart from customers."""

from ninja import Router

from jobs.models import Customer, ProductType
from resources.models import Site, WorkCentre
from routing.models import GateType, HoldReason, RouteTemplate

from .. import schemas
from ..auth import require_manage

router = Router(tags=["Reference data"])


@router.get("/sites", response=list[schemas.SiteOut], summary="List sites")
def list_sites(request):
    return Site.objects.all()


@router.get("/work-centres", response=list[schemas.WorkCentreOut], summary="List work centres")
def list_work_centres(request, include_inactive: bool = False):
    work_centres = WorkCentre.objects.all()
    return work_centres if include_inactive else work_centres.filter(is_active=True)


@router.get("/product-types", response=list[schemas.ProductTypeOut], summary="List product types and their spec fields")
def list_product_types(request):
    return ProductType.objects.all()


@router.get("/route-templates", response=list[schemas.RouteTemplateOut], summary="List route templates")
def list_route_templates(request):
    return RouteTemplate.objects.prefetch_related("steps__gate_types", "links")


@router.get("/hold-reasons", response=list[schemas.HoldReasonOut], summary="List hold reasons")
def list_hold_reasons(request):
    return HoldReason.objects.all()


@router.get("/gate-types", response=list[schemas.GateTypeOut], summary="List gate types")
def list_gate_types(request):
    return GateType.objects.all()


@router.get("/customers", response=list[schemas.CustomerOut], summary="List customers")
def list_customers(request, q: str = ""):
    customers = Customer.objects.all()
    return customers.filter(name__icontains=q) if q else customers


@router.post("/customers", response={200: schemas.CustomerOut, 201: schemas.CustomerOut},
             summary="Create a customer, or return the existing one with that name")
def create_customer(request, payload: schemas.CustomerIn):
    require_manage(request)
    customer, created = Customer.objects.get_or_create(name=payload.name.strip(), defaults={"notes": payload.notes})
    return (201 if created else 200), customer
