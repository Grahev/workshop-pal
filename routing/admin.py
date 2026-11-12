from django.contrib import admin

from .models import HoldReason, Operation, RouteTemplate, RouteTemplateStep


class RouteTemplateStepInline(admin.TabularInline):
    model = RouteTemplateStep
    extra = 1
    ordering = ("sequence",)


@admin.register(RouteTemplate)
class RouteTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "product_type", "is_active")
    list_filter = ("product_type", "is_active")
    inlines = [RouteTemplateStepInline]


@admin.register(HoldReason)
class HoldReasonAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")


@admin.register(Operation)
class OperationAdmin(admin.ModelAdmin):
    list_display = ("__str__", "work_centre", "status", "started_at", "completed_at")
    list_filter = ("status", "work_centre")
    search_fields = ("name", "item__name", "item__job__reference")
    filter_horizontal = ("depends_on",)
    # Projection fields change only through floor events.
    readonly_fields = (
        "status", "held_from", "hold_reason", "started_at", "completed_at",
        "status_changed_at", "rework_count",
    )
