from django.contrib import admin, messages

from .models import ApiKey


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "prefix", "is_active", "last_used_at", "created_at")
    list_filter = ("is_active",)
    readonly_fields = ("prefix", "created_at", "last_used_at")

    def save_model(self, request, obj, form, change):
        raw_key = None if change else obj.set_new_key()
        super().save_model(request, obj, form, change)
        if raw_key:
            messages.warning(request, f"Copy this API key now. It will not be shown again: {raw_key}")
