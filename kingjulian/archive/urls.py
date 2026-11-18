from django.urls import path
from . import delete_views as dv

from . import views as v

urlpatterns = [
    path("", v.ArchiveList.as_view(), name="archive_list"),
    path("<str:kind>/<int:pk>/", v.ArchivedDetail.as_view(), name="archive_detail"),
    path("<str:kind>/<int:pk>/restore/", v.ArchiveRestore.as_view(), name="archive_restore"),
    path("<str:kind>/<int:pk>/delete/", dv.ArchiveDelete.as_view(), name="archive_delete"),
]
