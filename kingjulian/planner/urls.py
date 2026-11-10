from django.urls import path

from core.crud import crud_urls

from . import views as v

urlpatterns = crud_urls("events", "event", v.EventList, v.EventCreate, v.EventUpdate, v.EventArchive) + [
    path("", v.CalendarView.as_view(), name="calendar"),
    path("agenda/", v.AgendaView.as_view(), name="agenda"),
    path("calendar.ics", v.IcsView.as_view(), name="calendar_ics"),
]
