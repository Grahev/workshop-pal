from django.contrib import admin

from .models import Attachment


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ("__str__", "category", "parent", "is_current", "is_archived", "uploaded_by", "uploaded_at")
    list_filter = ("category", "is_current", "is_archived", "content_type")
    search_fields = ("original_filename", "notes")
    readonly_fields = ("original_filename", "size", "uploaded_at")

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not change and not obj.uploaded_by:
            obj.uploaded_by = request.user
        super().save_model(request, obj, form, change)
