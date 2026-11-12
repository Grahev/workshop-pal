"""Querysets with everything the response schemas read already loaded."""

from routing.models import Gate, Operation


def operations():
    return Operation.objects.select_related("work_centre", "hold_reason", "item__job").prefetch_related(
        "dependencies__predecessor", "gates__gate_type"
    )


def gates():
    return Gate.objects.select_related("gate_type", "cleared_by", "item__job").prefetch_related("operations")
