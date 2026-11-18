"""Read-only pages. Edit pages keep their existing URLs (/properties/5/); these sit beside them at .../view/."""
from django.urls import path

from .views import RecordDetail

urlpatterns = [
    path("properties/<int:pk>/view/", RecordDetail.as_view(kind_key="property"), name="property_detail"),
    path("properties/units/<int:pk>/view/", RecordDetail.as_view(kind_key="unit"), name="unit_detail"),
    path("inventory/<int:pk>/view/", RecordDetail.as_view(kind_key="item"), name="inventoryitem_detail"),
    path("contacts/<int:pk>/view/", RecordDetail.as_view(kind_key="contact"), name="contact_detail"),
    path("maintenance/tasks/<int:pk>/view/", RecordDetail.as_view(kind_key="task"), name="task_detail"),
    path("planner/events/<int:pk>/view/", RecordDetail.as_view(kind_key="event"), name="event_detail"),
]
