"""Turn the ids, names and codes external systems send into records, with clear errors."""

from ninja.errors import HttpError

from jobs.models import Customer, ProductType
from resources.models import Site, WorkCentre
from routing.models import HoldReason, RouteTemplate


def _missing(what, value):
    return HttpError(422, f"{what} not found: {value}")


def customer(customer_id=None, name=None, create=True):
    if customer_id:
        found = Customer.objects.filter(pk=customer_id).first()
        if not found:
            raise _missing("Customer", customer_id)
        return found
    name = (name or "").strip()
    if not name:
        raise HttpError(422, "Give customer_id or customer (name).")
    if create:
        return Customer.objects.get_or_create(name=name)[0]
    found = Customer.objects.filter(name=name).first()
    if not found:
        raise _missing("Customer", name)
    return found


def site(site_id=None):
    found = Site.objects.filter(pk=site_id).first() if site_id else Site.objects.filter(is_active=True).first()
    if not found:
        raise HttpError(422, "Site not found. Create a site in Setup first." if not site_id else f"Site not found: {site_id}")
    return found


def product_type(product_type_id=None, name=None):
    if product_type_id:
        found = ProductType.objects.filter(pk=product_type_id).first()
        if not found:
            raise _missing("Product type", product_type_id)
        return found
    if name:
        found = ProductType.objects.filter(name__iexact=name.strip()).first()
        if not found:
            raise _missing("Product type", name)
        return found
    return None


def work_centre(work_centre_id=None, code=None, required=True):
    if work_centre_id:
        found = WorkCentre.objects.filter(pk=work_centre_id).first()
        if not found:
            raise _missing("Work centre", work_centre_id)
        return found
    if code:
        found = WorkCentre.objects.filter(code__iexact=code.strip()).first()
        if not found:
            raise _missing("Work centre", code)
        return found
    if required:
        raise HttpError(422, "Give work_centre_id or work_centre (code).")
    return None


def hold_reason(hold_reason_id=None, name=None):
    if hold_reason_id:
        found = HoldReason.objects.filter(pk=hold_reason_id).first()
        if not found:
            raise _missing("Hold reason", hold_reason_id)
        return found
    if name:
        found = HoldReason.objects.filter(name__iexact=name.strip()).first()
        if not found:
            raise _missing("Hold reason", name)
        return found
    return None


def route_template(template_id):
    found = RouteTemplate.objects.filter(pk=template_id).first()
    if not found:
        raise _missing("Route template", template_id)
    return found
