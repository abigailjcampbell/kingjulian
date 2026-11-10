from core.crud import (Filter, TenantArchiveView, TenantCreateView, TenantListView,
                       TenantUpdateView)

from .forms import PropertyForm, UnitForm
from .models import Property, Unit


def _property_choices():
    return [(p.pk, p.name) for p in Property.objects.active()]


class PropertyList(TenantListView):
    model = Property
    url_basename = "property"
    title = "Properties"
    columns = [("Name", "name"), ("Type", "get_property_type_display"),
               ("Address", "address_line1"), ("City", "city"), ("State", "state")]
    search_fields = ("name", "address_line1", "city", "postal_code", "notes")
    filters = [Filter("type", "property_type", "Type", lambda: list(Property.Type.choices))]


class PropertyCreate(TenantCreateView):
    model, form_class, url_basename, title = Property, PropertyForm, "property", "New property"


class PropertyUpdate(TenantUpdateView):
    model, form_class, url_basename, title = Property, PropertyForm, "property", "Edit property"


class PropertyArchive(TenantArchiveView):
    model, url_basename = Property, "property"


class UnitList(TenantListView):
    model = Unit
    url_basename = "unit"
    title = "Units"
    columns = [("Unit", "name"), ("Property", "property.name"), ("Notes", "notes")]
    search_fields = ("name", "property__name", "notes")
    filters = [Filter("property", "property_id", "Property", _property_choices)]

    def get_queryset(self):
        return super().get_queryset().select_related("property")


class UnitCreate(TenantCreateView):
    model, form_class, url_basename, title = Unit, UnitForm, "unit", "New unit"


class UnitUpdate(TenantUpdateView):
    model, form_class, url_basename, title = Unit, UnitForm, "unit", "Edit unit"


class UnitArchive(TenantArchiveView):
    model, url_basename = Unit, "unit"
