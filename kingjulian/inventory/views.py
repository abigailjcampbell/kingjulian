from core.crud import (Filter, TenantArchiveView, TenantCreateView, TenantListView,
                       TenantUpdateView)
from properties.models import Property

from .forms import InventoryItemForm
from .models import InventoryItem


class InventoryList(TenantListView):
    model = InventoryItem
    url_basename = "inventoryitem"
    title = "Inventory"
    export_key = "inventory"
    columns = [("Item", "name"), ("Property", "property.name"), ("Unit", "unit.name"),
               ("Category", "get_category_display"), ("Qty", "quantity"),
               ("Condition", "get_condition_display"), ("Warranty ends", "warranty_expires_on")]
    search_fields = ("name", "serial_number", "notes", "property__name", "unit__name")
    filters = [
        Filter("property", "property_id", "Property",
               lambda: [(p.pk, p.name) for p in Property.objects.active()]),
        Filter("category", "category", "Category", lambda: list(InventoryItem.Category.choices)),
        Filter("condition", "condition", "Condition", lambda: list(InventoryItem.Condition.choices)),
    ]

    def get_queryset(self):
        return super().get_queryset().select_related("property", "unit")


class InventoryCreate(TenantCreateView):
    model, form_class, url_basename, title = InventoryItem, InventoryItemForm, "inventoryitem", "New inventory item"


class InventoryUpdate(TenantUpdateView):
    model, form_class, url_basename, title = InventoryItem, InventoryItemForm, "inventoryitem", "Edit inventory item"


class InventoryArchive(TenantArchiveView):
    model, url_basename = InventoryItem, "inventoryitem"
