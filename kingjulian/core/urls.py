from django.urls import path

from planner.views import DashboardView

urlpatterns = [path("", DashboardView.as_view(), name="home")]
