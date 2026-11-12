from django.contrib import admin

from .models import Site, WorkCentre


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")


@admin.register(WorkCentre)
class WorkCentreAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "site", "display_order", "is_active")
    list_filter = ("site", "is_active")
    list_editable = ("display_order",)
