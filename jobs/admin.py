from django.contrib import admin, messages

from routing.models import Operation, RouteTemplate
from routing.services import apply_template

from .models import Customer, Job, JobItem, ProductType, SpecRevision


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    search_fields = ("name",)


@admin.register(ProductType)
class ProductTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")


class JobItemInline(admin.TabularInline):
    model = JobItem
    extra = 0
    fields = ("name", "product_type", "quantity", "parent")
    show_change_link = True


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("reference", "title", "customer", "due_date", "priority", "is_archived")
    list_filter = ("priority", "is_archived", "site", "customer")
    search_fields = ("reference", "title", "customer__name", "customer_reference")
    date_hierarchy = "due_date"
    inlines = [JobItemInline]

    def save_model(self, request, obj, form, change):
        if not change and not obj.created_by:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


class OperationInline(admin.TabularInline):
    model = Operation
    extra = 0
    fields = ("sequence", "name", "work_centre", "estimated_minutes", "status")
    # Status only changes through floor events, never by hand.
    readonly_fields = ("status",)
    ordering = ("sequence",)


@admin.register(JobItem)
class JobItemAdmin(admin.ModelAdmin):
    list_display = ("name", "job", "product_type", "quantity", "spec_version")
    list_filter = ("product_type",)
    search_fields = ("name", "job__reference")
    readonly_fields = ("spec_version",)
    inlines = [OperationInline]
    actions = ["apply_route_template"]

    @admin.action(description="Apply route template for the item's product type")
    def apply_route_template(self, request, queryset):
        for item in queryset:
            template = RouteTemplate.objects.filter(product_type=item.product_type, is_active=True).first()
            if template is None:
                self.message_user(request, f"No active template for {item}", messages.WARNING)
                continue
            if item.operations.exists():
                self.message_user(request, f"{item} already has a route; skipped", messages.WARNING)
                continue
            apply_template(item, template)
            self.message_user(request, f"Applied '{template}' to {item}", messages.SUCCESS)


@admin.register(SpecRevision)
class SpecRevisionAdmin(admin.ModelAdmin):
    list_display = ("item", "version", "changed_by", "changed_at")
    readonly_fields = ("item", "version", "spec", "changed_by", "changed_at", "note")

    def has_add_permission(self, request):
        return False
