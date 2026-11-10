from django.urls import path

from core.crud import crud_urls

from . import views as v

urlpatterns = (
    crud_urls("units", "unit", v.UnitList, v.UnitCreate, v.UnitUpdate, v.UnitArchive)
    + crud_urls("", "property", v.PropertyList, v.PropertyCreate, v.PropertyUpdate, v.PropertyArchive)
)
