"""Request and response shapes for the API."""

from datetime import date, datetime
from typing import Any, Literal, Optional

from ninja import Schema
from pydantic import Field

from jobs.models import Job

PriorityName = Literal["low", "normal", "high", "urgent"]
PRIORITY_VALUE = {
    "low": Job.Priority.LOW, "normal": Job.Priority.NORMAL,
    "high": Job.Priority.HIGH, "urgent": Job.Priority.URGENT,
}
PRIORITY_NAME = {value: name for name, value in PRIORITY_VALUE.items()}

EventType = Literal["start", "pause", "resume", "complete", "hold", "release", "rework", "skip", "note"]
TargetType = Literal["job", "item", "operation", "issue"]


class Message(Schema):
    detail: str


# ---------------------------------------------------------------- reference data

class SiteOut(Schema):
    id: int
    name: str
    is_active: bool


class WorkCentreOut(Schema):
    id: int
    site_id: int
    name: str
    code: str
    is_active: bool


class CustomerIn(Schema):
    name: str = Field(min_length=1, max_length=200)
    notes: str = ""


class CustomerOut(Schema):
    id: int
    name: str


class ProductTypeOut(Schema):
    id: int
    name: str
    spec_fields: list[Any]
    is_active: bool


class RouteTemplateStepOut(Schema):
    sequence: int
    name: str
    work_centre_id: int
    estimated_minutes: int


class RouteTemplateOut(Schema):
    id: int
    name: str
    product_type_id: Optional[int]
    is_active: bool
    steps: list[RouteTemplateStepOut]

    @staticmethod
    def resolve_steps(obj):
        return obj.steps.all()


class HoldReasonOut(Schema):
    id: int
    name: str
    is_active: bool


# ---------------------------------------------------------------- operations

class OperationIn(Schema):
    name: str = Field(min_length=1, max_length=100)
    work_centre_id: Optional[int] = None
    work_centre: Optional[str] = Field(None, description="Work centre code, as an alternative to work_centre_id")
    estimated_minutes: int = Field(0, ge=0)


class OperationPatch(Schema):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    work_centre_id: Optional[int] = None
    work_centre: Optional[str] = None
    estimated_minutes: Optional[int] = Field(None, ge=0)


class MoveIn(Schema):
    direction: Literal["up", "down"]


class OperationOut(Schema):
    id: int
    code: str = Field(description="Scan code printed on job sheets, OP-<id>")
    job_id: int
    job_reference: str
    item_id: int
    item_name: str
    sequence: int
    name: str
    work_centre_id: int
    work_centre: str = Field(description="Work centre code")
    estimated_minutes: int
    status: str
    hold_reason: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    status_changed_at: Optional[datetime]
    rework_count: int

    @staticmethod
    def resolve_code(obj):
        return f"OP-{obj.pk}"

    @staticmethod
    def resolve_job_id(obj):
        return obj.item.job_id

    @staticmethod
    def resolve_job_reference(obj):
        return obj.item.job.reference

    @staticmethod
    def resolve_item_name(obj):
        return obj.item.name

    @staticmethod
    def resolve_work_centre(obj):
        return obj.work_centre.code

    @staticmethod
    def resolve_hold_reason(obj):
        return obj.hold_reason.name if obj.hold_reason_id else None


class QueueOperationOut(OperationOut):
    is_late: bool
    waiting_on: list[str] = Field(description="Hard dependencies not yet done. Start is blocked until these finish.")
    earlier_steps: list[str] = Field(description="Earlier unfinished steps on the same item. Information only.")


class QueueOut(Schema):
    active: list[QueueOperationOut]
    on_hold: list[QueueOperationOut]
    ready: list[QueueOperationOut]
    upcoming: list[QueueOperationOut]


# ---------------------------------------------------------------- jobs and items

class ItemIn(Schema):
    name: str = Field(min_length=1, max_length=200)
    quantity: int = Field(1, ge=1)
    product_type_id: Optional[int] = None
    product_type: Optional[str] = Field(None, description="Product type name, as an alternative to product_type_id")
    spec: dict[str, Any] = {}
    route_template_id: Optional[int] = Field(None, description="Use this template for the route")
    operations: Optional[list[OperationIn]] = Field(None, description="Explicit route. Overrides any template.")
    apply_default_route: bool = Field(True, description="If no route is given, use the product type's template")


class ItemPatch(Schema):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    quantity: Optional[int] = Field(None, ge=1)
    product_type_id: Optional[int] = None
    product_type: Optional[str] = None


class SpecIn(Schema):
    spec: dict[str, Any]
    note: str = ""


class SpecRevisionOut(Schema):
    version: int
    spec: dict[str, Any]
    changed_at: datetime
    changed_by: Optional[str]
    note: str

    @staticmethod
    def resolve_changed_by(obj):
        return obj.changed_by.username if obj.changed_by_id else None


class ItemOut(Schema):
    id: int
    job_id: int
    job_reference: str
    name: str
    quantity: int
    product_type_id: Optional[int]
    product_type: Optional[str]
    spec: dict[str, Any]
    spec_version: int
    operations: list[OperationOut]

    @staticmethod
    def resolve_job_reference(obj):
        return obj.job.reference

    @staticmethod
    def resolve_product_type(obj):
        return obj.product_type.name if obj.product_type_id else None

    @staticmethod
    def resolve_operations(obj):
        return obj.operations.select_related("work_centre", "hold_reason", "item__job").order_by("sequence", "id")


class JobIn(Schema):
    reference: Optional[str] = Field(None, max_length=50, description="Job number. Generated if omitted. Must be unique.")
    title: str = Field(min_length=1, max_length=200)
    customer_id: Optional[int] = None
    customer: Optional[str] = Field(None, description="Customer name. Created if it does not exist.")
    customer_reference: str = Field("", max_length=100, description="Customer PO or order number")
    site_id: Optional[int] = Field(None, description="Defaults to the first active site")
    due_date: Optional[date] = None
    priority: PriorityName = "normal"
    notes: str = ""
    items: list[ItemIn] = []


class JobPatch(Schema):
    reference: Optional[str] = Field(None, min_length=1, max_length=50)
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    customer_id: Optional[int] = None
    customer: Optional[str] = None
    customer_reference: Optional[str] = Field(None, max_length=100)
    site_id: Optional[int] = None
    due_date: Optional[date] = None
    priority: Optional[PriorityName] = None
    notes: Optional[str] = None


class JobOut(Schema):
    id: int
    reference: str
    title: str
    customer: CustomerOut
    customer_reference: str
    site_id: int
    due_date: Optional[date]
    priority: str
    notes: str
    is_archived: bool
    created_at: datetime

    @staticmethod
    def resolve_priority(obj):
        return PRIORITY_NAME.get(obj.priority, str(obj.priority))


class JobDetailOut(JobOut):
    items: list[ItemOut]

    @staticmethod
    def resolve_items(obj):
        return obj.items.select_related("job", "product_type").order_by("id")


# ---------------------------------------------------------------- floor

class EventIn(Schema):
    type: EventType
    note: str = ""
    hold_reason_id: Optional[int] = Field(None, description="Required for hold, unless hold_reason is given")
    hold_reason: Optional[str] = Field(None, description="Hold reason name, as an alternative to hold_reason_id")
    work_centre_id: Optional[int] = Field(None, description="Where the event was recorded. Defaults to the operation's work centre.")
    occurred_at: Optional[datetime] = Field(None, description="When it happened. Defaults to now.")


class EventOut(Schema):
    id: int
    type: str
    operation_id: int
    operation_name: str
    item_id: int
    job_id: int
    job_reference: str
    user: Optional[str]
    work_centre_id: Optional[int]
    hold_reason: Optional[str]
    note: str
    occurred_at: datetime

    @staticmethod
    def resolve_operation_name(obj):
        return obj.operation.name

    @staticmethod
    def resolve_item_id(obj):
        return obj.operation.item_id

    @staticmethod
    def resolve_job_id(obj):
        return obj.operation.item.job_id

    @staticmethod
    def resolve_job_reference(obj):
        return obj.operation.item.job.reference

    @staticmethod
    def resolve_user(obj):
        return obj.user.username if obj.user_id else None

    @staticmethod
    def resolve_hold_reason(obj):
        return obj.hold_reason.name if obj.hold_reason_id else None


class EventResult(Schema):
    event: EventOut
    operation: OperationOut


class IssueIn(Schema):
    description: str = Field(min_length=1)
    category: Literal["issue", "ncr"] = "issue"
    work_centre_id: Optional[int] = None


class ResolveIn(Schema):
    resolution: str = ""


class IssueOut(Schema):
    id: int
    operation_id: int
    job_id: int
    job_reference: str
    category: str
    description: str
    is_open: bool
    raised_by: Optional[str]
    raised_at: datetime
    resolved_by: Optional[str]
    resolved_at: Optional[datetime]
    resolution: str

    @staticmethod
    def resolve_job_id(obj):
        return obj.operation.item.job_id

    @staticmethod
    def resolve_job_reference(obj):
        return obj.operation.item.job.reference

    @staticmethod
    def resolve_raised_by(obj):
        return obj.raised_by.username if obj.raised_by_id else None

    @staticmethod
    def resolve_resolved_by(obj):
        return obj.resolved_by.username if obj.resolved_by_id else None


# ---------------------------------------------------------------- board

class BoardSummaryOut(Schema):
    active: int
    in_progress: int
    on_hold: int
    late: int
    at_risk: int


class BoardRowOut(Schema):
    item_id: int
    item_name: str
    job_id: int
    job_reference: str
    customer: str
    due_date: Optional[date]
    priority: str
    state: str = Field(description="in_progress, paused, on_hold, pending, completed or no_route")
    current_operation: Optional[OperationOut]
    steps_done: int
    steps_total: int
    percent: int
    is_late: bool
    at_risk: bool
    held_since: Optional[datetime]


class BoardOut(Schema):
    summary: BoardSummaryOut
    items: list[BoardRowOut]


class LaneOut(Schema):
    work_centre: WorkCentreOut
    active: list[QueueOperationOut]
    on_hold: list[QueueOperationOut]
    ready: list[QueueOperationOut]
    upcoming: list[QueueOperationOut]


# ---------------------------------------------------------------- attachments

class AttachmentOut(Schema):
    id: int
    target_type: str
    target_id: int
    filename: str
    url: str
    size: int
    category: str
    revision: str
    is_current: bool
    is_archived: bool
    notes: str
    uploaded_by: Optional[str]
    uploaded_at: datetime
