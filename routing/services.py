"""Business logic for routes.

A route is a network of operations joined by links ("this comes after that")
and gates ("this needs hardware"). Everything that changes a route goes
through here, so the rules hold for the screens, the admin and the API alike:

- links never form a loop
- work that has started is never retrospectively blocked: restrictions can be
  relaxed on a started step, but not tightened
- steps with history are never deleted
"""

from collections import defaultdict

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import (
    Dependency,
    Enforcement,
    Gate,
    LinkKind,
    Operation,
    RouteTemplate,
    RouteTemplateLink,
    RouteTemplateStep,
)

PENDING = Operation.Status.PENDING


class RouteError(Exception):
    """The requested change to a route is not allowed."""


# ---------------------------------------------------------------- links

def _would_loop(operation, predecessor) -> bool:
    """True if `predecessor` already comes after `operation`, directly or through other steps."""
    comes_after = defaultdict(list)
    links = Dependency.objects.filter(operation__item_id=operation.item_id).values_list("operation_id", "predecessor_id")
    for successor_id, predecessor_id in links:
        comes_after[successor_id].append(predecessor_id)
    stack, seen = [predecessor.pk], set()
    while stack:
        current = stack.pop()
        if current == operation.pk:
            return True
        if current not in seen:
            seen.add(current)
            stack.extend(comes_after[current])
    return False


def _has_started(operation) -> bool:
    """Checked against the database, so a stale object cannot slip past the rules."""
    return Operation.objects.filter(pk=operation.pk).exclude(status=PENDING).exists()


def _check_not_tightening(operation, predecessor, enforcement):
    predecessor_done = Operation.objects.filter(pk=predecessor.pk, status__in=Operation.DONE_STATUSES).exists()
    if enforcement == Enforcement.STRICT and _has_started(operation) and not predecessor_done:
        raise RouteError(
            f"{operation.name} has already started, so it cannot be made to wait for {predecessor.name}. "
            "Add the link as advisory instead."
        )


@transaction.atomic
def add_link(operation, predecessor, kind=LinkKind.FINISH_TO_START, enforcement=None) -> Dependency:
    """Make `operation` come after `predecessor`. Updates the link if it already exists."""
    if predecessor.item_id != operation.item_id:
        raise RouteError("Steps can only be linked to steps on the same item.")
    if predecessor.pk == operation.pk:
        raise RouteError("A step cannot come after itself.")
    if _would_loop(operation, predecessor):
        raise RouteError(f"That would make a loop: {predecessor.name} already comes after {operation.name}.")
    enforcement = enforcement or operation.item.link_enforcement
    _check_not_tightening(operation, predecessor, enforcement)
    link, _ = Dependency.objects.update_or_create(
        operation=operation, predecessor=predecessor, defaults={"kind": kind, "enforcement": enforcement}
    )
    return link


def update_link(link, kind=None, enforcement=None) -> Dependency:
    """Change a link's type or enforcement."""
    kind = kind or link.kind
    enforcement = enforcement or link.enforcement
    tighter = (
        (enforcement == Enforcement.STRICT and link.enforcement == Enforcement.ADVISORY)
        or (kind == LinkKind.FINISH_TO_START and link.kind == LinkKind.FINISH_TO_FINISH)
    )
    if tighter:
        _check_not_tightening(link.operation, link.predecessor, enforcement)
    link.kind, link.enforcement = kind, enforcement
    link.save(update_fields=["kind", "enforcement"])
    return link


def remove_link(link) -> None:
    link.delete()


@transaction.atomic
def set_simple_sequence(item, enforcement=None) -> None:
    """Replace every link on the route with a plain chain: each step after the one before."""
    enforcement = enforcement or item.link_enforcement
    operations = list(item.operations.order_by("sequence", "id"))
    if enforcement == Enforcement.STRICT:
        for previous, current in zip(operations, operations[1:]):
            _check_not_tightening(current, previous, enforcement)
    Dependency.objects.filter(operation__item=item).delete()
    Dependency.objects.bulk_create(
        Dependency(operation=current, predecessor=previous, kind=LinkKind.FINISH_TO_START, enforcement=enforcement)
        for previous, current in zip(operations, operations[1:])
    )


def set_default_enforcement(item, enforcement) -> None:
    """Set the default for links added to this item's route from now on."""
    if enforcement not in Enforcement.values:
        raise RouteError("Enforcement must be strict or advisory.")
    item.link_enforcement = enforcement
    item.save(update_fields=["link_enforcement"])


# ---------------------------------------------------------------- gates

def require_gate(operation, gate_type) -> Gate:
    """Make an operation wait for a gate. The gate is created on the item if it is not there yet."""
    gate, _ = Gate.objects.get_or_create(item=operation.item, gate_type=gate_type)
    if _has_started(operation) and not gate.is_cleared:
        raise RouteError(f"{operation.name} has already started, so it cannot be made to wait for {gate.name}.")
    operation.gates.add(gate)
    return gate


def drop_gate_requirement(operation, gate) -> None:
    operation.gates.remove(gate)


@transaction.atomic
def set_required_gates(operation, gate_types) -> None:
    """Make the operation need exactly these gate types."""
    wanted = {gate_type.pk: gate_type for gate_type in gate_types}
    for gate in operation.gates.all():
        if gate.gate_type_id not in wanted:
            operation.gates.remove(gate)
    have = set(operation.gates.values_list("gate_type_id", flat=True))
    for pk, gate_type in wanted.items():
        if pk not in have:
            require_gate(operation, gate_type)


def _note_on_waiting_operations(gate, text, user):
    from floor.services import add_note  # floor depends on routing, so import late

    for operation in gate.operations.exclude(status__in=Operation.DONE_STATUSES):
        add_note(operation, text, user=user)


@transaction.atomic
def clear_gate(gate, user=None, note="") -> Gate:
    """Mark a gate as met, e.g. the hardware has arrived. Steps waiting on it can now start."""
    if not gate.is_cleared:
        gate.is_cleared = True
        gate.cleared_at = timezone.now()
        gate.cleared_by = user
        gate.note = note
        gate.save()
        _note_on_waiting_operations(gate, f"{gate.name} available. {note}".strip(), user)
    return gate


@transaction.atomic
def reopen_gate(gate, user=None, note="") -> Gate:
    """Mark a gate as not met again. Steps that have already started carry on."""
    if gate.is_cleared:
        gate.is_cleared = False
        gate.cleared_at = None
        gate.cleared_by = None
        gate.note = note
        gate.save()
        _note_on_waiting_operations(gate, f"{gate.name} no longer available. {note}".strip(), user)
    return gate


def set_gate_expected(gate, expected_date) -> Gate:
    gate.expected_date = expected_date
    gate.save(update_fields=["expected_date"])
    return gate


def remove_gate(gate) -> None:
    """Take a gate off an item altogether."""
    gate.delete()


# ---------------------------------------------------------------- steps

@transaction.atomic
def add_operation(item, name, work_centre, estimated_minutes=0, after=None,
                  kind=LinkKind.FINISH_TO_START, enforcement=None, gate_types=None) -> Operation:
    """Add a step to an item's route.

    `after` is the steps it comes after; leave it empty for a step that can start straight away.
    """
    last = item.operations.aggregate(last=Max("sequence"))["last"] or 0
    operation = Operation.objects.create(
        item=item, sequence=last + 10, name=name, work_centre=work_centre, estimated_minutes=estimated_minutes
    )
    for predecessor in after or []:
        add_link(operation, predecessor, kind=kind, enforcement=enforcement)
    for gate_type in gate_types or []:
        require_gate(operation, gate_type)
    return operation


@transaction.atomic
def add_steps(item, steps) -> list[Operation]:
    """Add several steps to a route, resolving the links between them.

    Each step is a dict: name, work_centre, estimated_minutes, and optionally
    gate_types and after. `after` is a list of (step, kind, enforcement), where
    step is an operation, an operation id, or the name of a step on this item
    (including one earlier in the same list).

    A step with no `after` key follows the step before it, so a plain list is a
    simple sequence. `after: []` makes a step that can start straight away.
    """
    existing = list(item.operations.order_by("sequence", "id"))
    by_id = {op.pk: op for op in existing}
    by_name = {op.name.lower(): op for op in existing}
    previous = existing[-1] if existing else None
    created = []

    def find(ref, step_name):
        if isinstance(ref, Operation):
            return ref
        found = by_id.get(ref) if isinstance(ref, int) else by_name.get(str(ref).strip().lower())
        if found is None:
            raise RouteError(f"'{step_name}' is set to come after '{ref}', which is not an earlier step on this item.")
        return found

    for step in steps:
        after = step.get("after")
        operation = add_operation(
            item, step["name"], step["work_centre"], step.get("estimated_minutes", 0),
            gate_types=step.get("gate_types"),
        )
        if after is None:
            links = [(previous, LinkKind.FINISH_TO_START, None)] if previous else []
        else:
            links = [(find(ref, step["name"]), kind or LinkKind.FINISH_TO_START, enforcement) for ref, kind, enforcement in after]
        for predecessor, kind, enforcement in links:
            add_link(operation, predecessor, kind=kind, enforcement=enforcement)
        by_id[operation.pk] = operation
        by_name[operation.name.lower()] = operation
        previous = operation
        created.append(operation)
    return created


@transaction.atomic
def move_operation(operation, direction) -> None:
    """Move a step up or down the display order. Links decide what can start, not the order."""
    siblings = list(operation.item.operations.select_for_update().order_by("sequence", "id"))
    index = next(i for i, op in enumerate(siblings) if op.pk == operation.pk)
    target = index - 1 if direction == "up" else index + 1
    if target < 0 or target >= len(siblings):
        return
    siblings[index], siblings[target] = siblings[target], siblings[index]
    for position, op in enumerate(siblings, start=1):
        if op.sequence != position * 10:
            op.sequence = position * 10
            op.save(update_fields=["sequence"])


@transaction.atomic
def remove_operation(operation) -> None:
    """Delete a step that nothing has happened to yet, joining what came before it to what came after.

    Anything with history is skipped instead, which keeps it in the record and
    counts as done for the steps that follow.
    """
    if operation.status != PENDING or operation.events.exists():
        raise RouteError("This step has history, so it cannot be removed. Skip it instead.")
    before = list(operation.dependencies.select_related("predecessor"))
    after = list(operation.dependents.select_related("operation"))
    for following in after:
        for preceding in before:
            Dependency.objects.get_or_create(
                operation=following.operation,
                predecessor=preceding.predecessor,
                defaults={"kind": following.kind, "enforcement": following.enforcement},
            )
    operation.delete()


def update_operation(operation, name, work_centre, estimated_minutes) -> Operation:
    """Change the details of a step. Only before work has started on it."""
    if operation.status != PENDING:
        raise RouteError("Only steps that have not started can be changed.")
    operation.name = name
    operation.work_centre = work_centre
    operation.estimated_minutes = estimated_minutes
    operation.save(update_fields=["name", "work_centre", "estimated_minutes"])
    return operation


# ---------------------------------------------------------------- templates

@transaction.atomic
def apply_template(item, template: RouteTemplate) -> list[Operation]:
    """Copy a template onto an item: steps, links and gates. The copies are then free to be edited."""
    steps = list(template.steps.select_related("work_centre").prefetch_related("gate_types").order_by("sequence", "id"))
    by_step = {}
    for step in steps:
        by_step[step.pk] = Operation.objects.create(
            item=item, sequence=step.sequence, name=step.name,
            work_centre=step.work_centre, estimated_minutes=step.estimated_minutes,
        )
        for gate_type in step.gate_types.all():
            require_gate(by_step[step.pk], gate_type)

    if template.simple_sequence:
        links = [
            Dependency(operation=by_step[current.pk], predecessor=by_step[previous.pk],
                       kind=LinkKind.FINISH_TO_START, enforcement=template.default_enforcement)
            for previous, current in zip(steps, steps[1:])
        ]
    else:
        links = [
            Dependency(operation=by_step[link.step_id], predecessor=by_step[link.predecessor_id],
                       kind=link.kind, enforcement=link.enforcement)
            for link in template.links.all()
            if link.step_id in by_step and link.predecessor_id in by_step
        ]
    Dependency.objects.bulk_create(links)

    item.link_enforcement = template.default_enforcement
    item.save(update_fields=["link_enforcement"])
    return list(by_step.values())


@transaction.atomic
def save_as_template(item, name, product_type=None) -> RouteTemplate:
    """Turn an item's route, with its links and gates, into a reusable template."""
    name = (name or "").strip()
    if not name:
        raise RouteError("Give the template a name.")
    operations = list(item.operations.prefetch_related("gates__gate_type").order_by("sequence", "id"))
    if not operations:
        raise RouteError("This item has no route to save.")
    template = RouteTemplate.objects.create(
        name=name, product_type=product_type, simple_sequence=False, default_enforcement=item.link_enforcement
    )
    by_operation = {}
    for position, operation in enumerate(operations, start=1):
        step = RouteTemplateStep.objects.create(
            template=template, sequence=position * 10, name=operation.name,
            work_centre=operation.work_centre, estimated_minutes=operation.estimated_minutes,
        )
        step.gate_types.set([gate.gate_type for gate in operation.gates.all()])
        by_operation[operation.pk] = step
    RouteTemplateLink.objects.bulk_create(
        RouteTemplateLink(template=template, step=by_operation[link.operation_id],
                          predecessor=by_operation[link.predecessor_id], kind=link.kind, enforcement=link.enforcement)
        for link in Dependency.objects.filter(operation__item=item)
    )
    return template


# ---------------------------------------------------------------- reading a route

def route_operations(item):
    """An item's operations with links and gates loaded, in display order."""
    return (
        item.operations.select_related("work_centre", "hold_reason")
        .prefetch_related("dependencies__predecessor", "gates__gate_type")
        .order_by("sequence", "id")
    )


def route_stages(operations) -> list[list[Operation]]:
    """Group operations into stages for a preview: everything in a stage can run side by side.

    A step's stage is one more than the latest stage among the steps that must
    finish before it starts. "Finish before finish" links do not push a step
    later, because both steps can be worked on together.
    """
    operations = list(operations)
    by_pk = {op.pk: op for op in operations}
    stage_of = {}

    def stage(op, trail=()):
        if op.pk not in stage_of:
            before = [
                by_pk[link.predecessor_id] for link in op.dependencies.all()
                if link.kind == LinkKind.FINISH_TO_START
                and link.predecessor_id in by_pk and link.predecessor_id not in trail
            ]
            stage_of[op.pk] = 1 + max((stage(p, trail + (op.pk,)) for p in before), default=0)
        return stage_of[op.pk]

    stages = defaultdict(list)
    for op in operations:
        stages[stage(op)].append(op)
    return [stages[number] for number in sorted(stages)]
