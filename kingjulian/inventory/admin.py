from django.contrib import admin

from core.admin import TenantAdmin

from .models import InventoryItem


@admin.register(InventoryItem)
class InventoryItemAdmin(TenantAdmin):
    list_display = ("name", "property", "category", "quantity", "condition",
                    "warranty_expires_on", "account", "archived_at")
    search_fields = ("name", "serial_number")
