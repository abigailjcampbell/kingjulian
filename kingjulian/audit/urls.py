from django.urls import path

from . import views as v

urlpatterns = [
    path("", v.ActivityList.as_view(), name="activity_list"),
    path("<int:pk>/", v.ActivityJump.as_view(), name="activity_edit"),
]
