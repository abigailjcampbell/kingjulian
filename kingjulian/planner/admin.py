from django.contrib import admin

from core.admin import TenantAdmin

from .models import Event


@admin.register(Event)
class EventAdmin(TenantAdmin):
    list_display = ("title", "kind", "starts_on", "property", "account", "archived_at")
    search_fields = ("title", "notes")
