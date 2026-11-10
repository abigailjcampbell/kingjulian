from datetime import date, timedelta

from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from core.crud import (Filter, TenantArchiveView, TenantCreateView, TenantListView, TenantUpdateView,
                       TenantViewMixin)
from properties.models import Property

from .forms import EventForm
from .ical import build_ics
from .models import Event
from .services import (AGENDA_CHOICES, calendar_entries, agenda, dashboard, grid_weeks, month_grid,
                       month_label_key, overdue_tasks, parse_date, parse_days, parse_month, shift_month)

ICS_DAYS_BACK, ICS_DAYS_AHEAD = 31, 366


# ---- events (manual calendar entries) -----------------------------------------------------------
def _apply_when(qs, value):
    today = timezone.localdate()
    live = Q(starts_on__gte=today) | Q(ends_on__gte=today)
    if value == "upcoming":
        return qs.filter(live).order_by("starts_on", "start_time", "id")
    return qs.exclude(live).order_by("-starts_on", "-start_time", "-id")


class EventList(TenantListView):
    model = Event
    url_basename = "event"
    title = "Events"
    columns = [("Event", "title"), ("Date", "date_label"), ("Time", "time_label"), ("Type", "get_kind_display"),
               ("Property", "property.name"), ("With", "contact.name")]
    search_fields = ("title", "notes", "property__name", "contact__name")
    filters = [
        Filter("when", "", "When", lambda: [("upcoming", "Upcoming"), ("past", "Past")], default="upcoming",
               apply=_apply_when),
        Filter("kind", "kind", "Type", lambda: list(Event.Kind.choices)),
        Filter("property", "property_id", "Property", lambda: [(p.pk, p.name) for p in Property.objects.active()]),
    ]

    def get_queryset(self):
        return super().get_queryset().select_related("property", "contact")


class EventCreate(TenantCreateView):
    model, form_class, url_basename, title = Event, EventForm, "event", "New event"

    def get_initial(self):
        # The calendar's "+" links pass ?date=YYYY-MM-DD; garbage is ignored.
        initial = super().get_initial()
        day = parse_date(self.request.GET.get("date"), None)
        if day:
            initial["starts_on"] = day
        return initial


class EventUpdate(TenantUpdateView):
    model, form_class, url_basename, title = Event, EventForm, "event", "Edit event"


class EventArchive(TenantArchiveView):
    model, url_basename = Event, "event"


# ---- dashboard -----------------------------------------------------------------------------------
class DashboardView(TenantViewMixin, TemplateView):
    template_name = "planner/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = timezone.localdate()
        ctx.update(d=dashboard(today, can_write=self.request.membership.can_write), today=today)
        return ctx


# ---- calendar ------------------------------------------------------------------------------------
WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


class CalendarView(TenantViewMixin, TemplateView):
    template_name = "planner/calendar.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = timezone.localdate()
        year, month = parse_month(self.request.GET.get("month"), today)
        weeks = grid_weeks(year, month)
        entries = calendar_entries(weeks[0][0], weeks[-1][-1], today=today,
                                   can_write=self.request.membership.can_write)
        ctx.update(
            grid=month_grid(year, month, entries, today), weekdays=WEEKDAYS,
            title=date(year, month, 1).strftime("%B %Y"),
            prev_month=month_label_key(*shift_month(year, month, -1)),
            next_month=month_label_key(*shift_month(year, month, 1)),
            this_month=month_label_key(today.year, today.month),
            is_current_month=(year, month) == (today.year, today.month),
            month_key=month_label_key(year, month),
            first_of_month=date(year, month, 1),
        )
        return ctx


class AgendaView(TenantViewMixin, TemplateView):
    template_name = "planner/agenda.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = timezone.localdate()
        start = parse_date(self.request.GET.get("from"), today)
        days = parse_days(self.request.GET.get("days"))
        can_write = self.request.membership.can_write
        overdue_count, overdue = overdue_tasks(today) if start <= today else (0, [])
        ctx.update(groups=agenda(start, days, today=today, can_write=can_write), start=start, days=days,
                   end=start + timedelta(days=days - 1), choices=AGENDA_CHOICES, today=today,
                   overdue=overdue, overdue_count=overdue_count,
                   prev_start=start - timedelta(days=days), next_start=start + timedelta(days=days))
        return ctx


class IcsView(TenantViewMixin, View):
    """Download the next year of the calendar as .ics (import into Google/Apple/Outlook)."""

    def get(self, request):
        today = timezone.localdate()
        entries = calendar_entries(today - timedelta(days=ICS_DAYS_BACK), today + timedelta(days=ICS_DAYS_AHEAD),
                                   today=today, can_write=False)
        body = build_ics(entries, account_name=request.account.name, account_id=request.account.pk,
                         now=timezone.now())
        response = HttpResponse(body, content_type="text/calendar; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="property-calendar.ics"'
        response["Cache-Control"] = "private, no-store"
        return response
