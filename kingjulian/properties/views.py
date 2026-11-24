from django.shortcuts import get_object_or_404
from django.urls import reverse
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
    export_key = "properties"
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


class PropertyUnits(TenantListView):
    """A property's units: a subsection of the property, not a top-level list."""
    model = Unit
    url_basename = "unit"
    export_key = "units"
    columns = [("Unit", "name"), ("Notes", "notes")]
    search_fields = ("name", "notes")

    def dispatch(self, request, *args, **kwargs):
        self.parent = None
        return super().dispatch(request, *args, **kwargs)

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.property_pk = kwargs["property_pk"]

    def get_property(self):
        if self.parent is None:
            self.parent = get_object_or_404(Property.objects.active(), pk=self.property_pk)   # tenant-scoped: other accounts' pks 404
        return self.parent

    @property
    def title(self):
        return f"Units · {self.get_property().name}"

    @property
    def back_url(self):
        return reverse("property_detail", args=[self.property_pk])

    @property
    def back_label(self):
        return self.get_property().name

    def get_queryset(self):
        self.get_property()
        return super().get_queryset().filter(property_id=self.property_pk)

    def get_create_url(self):
        return f"{reverse('unit_create')}?property={self.property_pk}"


class UnitFormMixin:
    """Units have no list of their own: 'back' means the property they belong to."""

    def get_list_url(self):
        obj = getattr(self, "object", None)
        wanted = obj.property_id if obj is not None and obj.pk else self.request.GET.get("property", "")
        if str(wanted).isdigit() and Property.objects.active().filter(pk=wanted).exists():      # only this account's live properties
            return reverse("property_detail", args=[int(wanted)])
        return reverse("property_list")


class UnitCreate(UnitFormMixin, TenantCreateView):
    model, form_class, url_basename, title = Unit, UnitForm, "unit", "New unit"

    def get_initial(self):
        initial = super().get_initial()
        wanted = self.request.GET.get("property", "")
        if wanted.isdigit() and Property.objects.active().filter(pk=wanted).exists():
            initial["property"] = int(wanted)
        return initial

    def get_success_url(self):
        return reverse("property_units", args=[self.object.property_id])      # you were adding units: show the list you added to


class UnitUpdate(UnitFormMixin, TenantUpdateView):
    model, form_class, url_basename, title = Unit, UnitForm, "unit", "Edit unit"


class UnitArchive(TenantArchiveView):
    model, url_basename = Unit, "unit"

    def after_archive_url(self, obj):
        return reverse("property_detail", args=[obj.property_id])      # back to the property it belonged to