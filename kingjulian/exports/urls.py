from django.urls import path

from . import views as v

urlpatterns = [
    path("", v.ExportHub.as_view(), name="export_hub"),
    path("all.zip", v.ExportAll.as_view(), name="export_all"),
    path("<slug:key>.csv", v.ExportDataset.as_view(), name="export_dataset"),
]
