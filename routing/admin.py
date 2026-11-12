from django.contrib import admin

from .models import Dependency, Gate, GateType, HoldReason, Operation, RouteTemplate, RouteTemplateLink, RouteTemplateStep


class RouteTemplateStepInline(admin.TabularInline):
    model = RouteTemplateStep
    extra = 1
    ordering = ("sequence",)


class RouteTemplateLinkInline(admin.TabularInline):
    """Links between a template's steps. Only used when "simple sequence" is unticked."""

    model = RouteTemplateLink
    extra = 1
    verbose_name = "link"
    verbose_name_plural = "links (used when 'simple sequence' is unticked; save new steps first)"

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # Only offer this template's own steps.
        if db_field.name in ("step", "predecessor"):
            template_id = request.resolver_match.kwargs.get("object_id")
            kwargs["queryset"] = RouteTemplateStep.objects.filter(template_id=template_id or 0).order_by("sequence")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(RouteTemplate)
class RouteTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "product_type", "simple_sequence", "default_enforcement", "is_active")
    list_filter = ("product_type", "is_active")
    inlines = [RouteTemplateStepInline, RouteTemplateLinkInline]


@admin.register(HoldReason)
class HoldReasonAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")


@admin.register(GateType)
class GateTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")


class DependencyInline(admin.TabularInline):
    model = Dependency
    fk_name = "operation"
    extra = 0
    verbose_name = "comes after"
    verbose_name_plural = "comes after"


@admin.register(Operation)
class OperationAdmin(admin.ModelAdmin):
    list_display = ("__str__", "work_centre", "status", "started_at", "completed_at")
    list_filter = ("status", "work_centre")
    search_fields = ("name", "item__name", "item__job__reference")
    inlines = [DependencyInline]
    # Projection fields change only through floor events.
    readonly_fields = (
        "status", "held_from", "hold_reason", "started_at", "completed_at",
        "status_changed_at", "rework_count", "gates",
    )


@admin.register(Gate)
class GateAdmin(admin.ModelAdmin):
    list_display = ("gate_type", "item", "is_cleared", "expected_date", "cleared_at")
    list_filter = ("is_cleared", "gate_type")
    readonly_fields = ("cleared_at", "cleared_by")
