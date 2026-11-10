from django.contrib import admin

from core.admin import TenantAdmin

from .models import Property, Unit


@admin.register(Property)
class PropertyAdmin(TenantAdmin):
    list_display = ("name", "property_type", "city", "account", "archived_at")
    search_fields = ("name", "address_line1", "city")


@admin.register(Unit)
class UnitAdmin(TenantAdmin):
    list_display = ("name", "property", "account", "archived_at")
