from django.views.generic import TemplateView

from contacts.models import Contact
from inventory.models import InventoryItem
from properties.models import Property, Unit

from .crud import TenantViewMixin


class HomeView(TenantViewMixin, TemplateView):
    template_name = "home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["counts"] = [
            ("Properties", Property.objects.active().count(), "property_list"),
            ("Units", Unit.objects.active().count(), "unit_list"),
            ("Contacts", Contact.objects.active().count(), "contact_list"),
            ("Inventory items", InventoryItem.objects.active().count(), "inventoryitem_list"),
        ]
        return ctx
