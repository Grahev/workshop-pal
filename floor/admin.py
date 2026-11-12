from django.contrib import admin

from .models import Event, Issue


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    """Read-only view of the event log. Events are created through floor.services."""

    list_display = ("occurred_at", "type", "operation", "user", "work_centre", "hold_reason")
    list_filter = ("type", "work_centre")
    search_fields = ("operation__name", "operation__item__job__reference", "note")
    date_hierarchy = "occurred_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Issue)
class IssueAdmin(admin.ModelAdmin):
    list_display = ("__str__", "category", "raised_by", "raised_at", "resolved_at")
    list_filter = ("category",)
    search_fields = ("description", "operation__item__job__reference")
