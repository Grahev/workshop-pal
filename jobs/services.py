"""Business logic for jobs. Views, admin and the API should call these, not edit models directly."""

from django.db import transaction

from .models import JobItem, SpecRevision

SPEC_PREFIX = "spec__"


@transaction.atomic
def update_spec(item: JobItem, spec: dict, user=None, note: str = "") -> JobItem:
    """Change an item's spec, keeping the previous version in SpecRevision."""
    item = JobItem.objects.select_for_update().get(pk=item.pk)
    if spec == item.spec:
        return item
    SpecRevision.objects.create(
        item=item, version=item.spec_version, spec=item.spec, changed_by=user, note=note
    )
    item.spec = spec
    item.spec_version += 1
    item.save(update_fields=["spec", "spec_version"])
    return item


def spec_form_fields(item: JobItem) -> list[dict]:
    """Field definitions for an item's spec form.

    Fields come from the product type. Values already on the item that the
    product type does not define are kept and shown as plain text fields.
    Supported types: text, number, textarea, choice (with a "choices" list).
    """
    fields = []
    defined = set()
    for definition in (item.product_type.spec_fields if item.product_type else None) or []:
        if not isinstance(definition, dict) or not definition.get("key"):
            continue
        key = definition["key"]
        defined.add(key)
        fields.append({
            "key": key,
            "name": SPEC_PREFIX + key,
            "label": definition.get("label") or key,
            "type": definition.get("type", "text"),
            "choices": definition.get("choices") or [],
            "required": bool(definition.get("required")),
            "value": item.spec.get(key, ""),
        })
    for key, value in item.spec.items():
        if key not in defined:
            fields.append({
                "key": key, "name": SPEC_PREFIX + key, "label": key.replace("_", " ").capitalize(),
                "type": "text", "choices": [], "required": False, "value": value,
            })
    return fields


def _coerce(value: str, field_type: str):
    if field_type == "number":
        try:
            number = float(value)
        except ValueError:
            return value
        return int(number) if number.is_integer() else number
    return value


def spec_from_post(item: JobItem, post) -> tuple[dict, list[str]]:
    """Build a spec dict from a submitted spec form. Returns (spec, error messages)."""
    spec, errors = {}, []
    for field in spec_form_fields(item):
        value = (post.get(field["name"]) or "").strip()
        if value:
            spec[field["key"]] = _coerce(value, field["type"])
        elif field["required"]:
            errors.append(f"{field['label']} is required.")
    extra_key = (post.get("extra_key") or "").strip().lower().replace(" ", "_")
    extra_value = (post.get("extra_value") or "").strip()
    if extra_key and extra_value:
        spec[extra_key] = extra_value
    return spec, errors
