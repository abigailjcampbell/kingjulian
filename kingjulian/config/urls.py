from django.contrib import admin
from django.urls import include, path
from planner.views import DashboardView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("properties/", include("properties.urls")),
    path("contacts/", include("contacts.urls")),
    path("inventory/", include("inventory.urls")),
    path("", include("core.urls")),
    path("maintenance/", include("maintenance.urls")),
    path("planner/", include("planner.urls")),
    path("files/", include("attachments.urls")),
    path("activity/", include("audit.urls")),
    path("exports/", include("exports.urls")),
]
