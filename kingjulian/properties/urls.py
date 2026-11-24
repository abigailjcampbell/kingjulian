from django.urls import path
from django.views.generic import RedirectView

from core.crud import crud_urls

from . import views as v

urlpatterns = [
    # Units live under their property. The old top-level list is gone; its URL sends people to Properties.
    path("units/", RedirectView.as_view(pattern_name="property_list", query_string=False), name="units_moved"),
    path("units/new/", v.UnitCreate.as_view(), name="unit_create"),
    path("units/<int:pk>/", v.UnitUpdate.as_view(), name="unit_edit"),
    path("units/<int:pk>/archive/", v.UnitArchive.as_view(), name="unit_archive"),
    path("<int:property_pk>/units/", v.PropertyUnits.as_view(), name="property_units"),
] + crud_urls("", "property", v.PropertyList, v.PropertyCreate, v.PropertyUpdate, v.PropertyArchive)
