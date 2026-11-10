"""Read-only views over the data: the calendar feed, month grid, agenda, and dashboard figures.

The calendar is a *query*, not a table. Events live in `Event`; task due dates come from
`TaskOccurrence`; warranty expirations come from `InventoryItem`. Nothing is copied, so nothing can
go stale. Everything here runs under the active tenant context (the default managers are scoped), and
the number of queries is fixed regardless of how much data there is.
"""
import calendar
import re
from dataclasses import dataclass, field
from datetime import date, time, timedelta

from django.db.models import Count, Q, Sum
from django.urls import reverse

from inventory.models import InventoryItem
from maintenance.models import TaskOccurrence
from properties.models import Property, Unit
from contacts.models import Contact

from .models import Event

OPEN, DONE = TaskOccurrence.Status.OPEN, TaskOccurrence.Status.DONE
KIND_ORDER = {"event": 0, "task": 1, "warranty": 2}
WARRANTY_WINDOW_DAYS = 60
RECENTLY_EXPIRED_DAYS = 30
SPEND_WINDOW_DAYS = 365
MAX_PER_DAY = 3


@dataclass(frozen=True)
class Entry:
    kind: str                       # "event" | "task" | "warranty"
    uid: str                        # stable across requests (used by the .ics export)
    title: str
    start: date
    end: date                       # inclusive last day
    start_time: time | None = None
    end_time: time | None = None
    where: str = ""
    url: str | None = None          # None when this user may not open the target page
    status: str = ""                # task: open|done|overdue   warranty: expiring|expired
    detail: str = ""
    label: str = ""                 # short type label for chips and iCal categories


def _sort_key(e: Entry):
    return (e.start, e.start_time or time.min, KIND_ORDER[e.kind], e.title.lower(), e.uid)


def calendar_entries(start: date, end: date, *, today: date, can_write: bool) -> list[Entry]:
    """Every dated thing that touches [start, end] (inclusive), in display order. Three queries."""
    out: list[Entry] = []

    events = (Event.objects.active().filter(starts_on__lte=end)
              .filter(Q(ends_on__gte=start) | Q(ends_on__isnull=True, starts_on__gte=start))
              .select_related("property", "contact"))
    for ev in events:
        detail = "\n".join(filter(None, [f"With: {ev.contact.name}" if ev.contact else "", ev.notes]))
        out.append(Entry("event", f"event-{ev.pk}", ev.title, ev.starts_on, ev.last_day, ev.start_time, ev.end_time,
                         ev.property.name if ev.property else "",
                         reverse("event_edit", args=[ev.pk]) if can_write else None,
                         "", detail, ev.get_kind_display()))

    occurrences = (TaskOccurrence.objects
                   .filter(due_date__range=(start, end), status__in=[OPEN, DONE], task__archived_at__isnull=True)
                   .select_related("task__property", "task__item"))
    for o in occurrences:
        status = "done" if o.status == DONE else ("overdue" if o.due_date < today else "open")
        where = " / ".join(filter(None, [o.task.property.name, o.task.item.name if o.task.item else ""]))
        out.append(Entry("task", f"task-{o.pk}", o.task.title, o.due_date, o.due_date, None, None, where,
                         reverse("occurrence_edit", args=[o.pk]), status, o.task.description, "Maintenance"))

    items = (InventoryItem.objects.active().filter(warranty_expires_on__range=(start, end))
             .select_related("property", "unit"))
    for it in items:
        status = "expired" if it.warranty_expires_on < today else "expiring"
        detail = f"Serial: {it.serial_number}" if it.serial_number else ""
        where = " / ".join(filter(None, [it.property.name, it.unit.name if it.unit else ""]))   # tells identical items apart
        out.append(Entry("warranty", f"warranty-{it.pk}", f"Warranty ends: {it.name}", it.warranty_expires_on,
                         it.warranty_expires_on, None, None, where,
                         reverse("inventoryitem_edit", args=[it.pk]) if can_write else None, status, detail, "Warranty"))

    return sorted(out, key=_sort_key)


# ---- month grid ----------------------------------------------------------------------------------
MONTH_RE = re.compile(r"([0-9]{4})-([0-9]{2})")      # ASCII digits only (\d also matches e.g. fullwidth ４)


def parse_month(text, today: date) -> tuple[int, int]:
    """'2026-11' -> (2026, 11). Anything malformed or out of range falls back to today's month."""
    m = MONTH_RE.fullmatch(text or "")
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if 1900 <= year <= 2200 and 1 <= month <= 12:
            return year, month
    return today.year, today.month


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def month_label_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def grid_weeks(year: int, month: int) -> list[list[date]]:
    """Sunday-first weeks covering the month, padded with neighbouring days to whole weeks."""
    return calendar.Calendar(firstweekday=6).monthdatescalendar(year, month)


def month_grid(year: int, month: int, entries: list[Entry], today: date, max_per_day: int = MAX_PER_DAY):
    """Weeks of day cells. A multi-day entry appears on every day it covers."""
    weeks = grid_weeks(year, month)
    first, last = weeks[0][0], weeks[-1][-1]
    by_day: dict[date, list[Entry]] = {}
    for e in entries:
        day, stop = max(e.start, first), min(e.end, last)
        while day <= stop:
            by_day.setdefault(day, []).append(e)
            day += timedelta(days=1)
    grid = []
    for week in weeks:
        row = []
        for d in week:
            items = by_day.get(d, [])
            row.append({"date": d, "in_month": d.month == month, "is_today": d == today,
                        "entries": items[:max_per_day], "more": max(0, len(items) - max_per_day),
                        "total": len(items)})
        grid.append(row)
    return grid


# ---- agenda --------------------------------------------------------------------------------------
AGENDA_CHOICES = (7, 14, 30, 60, 90)


def parse_days(text, default: int = 30) -> int:
    try:
        n = int(text)
    except (TypeError, ValueError):
        return default
    return n if n in AGENDA_CHOICES else default


def parse_date(text, default: date) -> date:
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        return default


def agenda(start: date, days: int, *, today: date, can_write: bool):
    """Entries for [start, start+days) grouped by date. Each appears once, on its first visible day."""
    end = start + timedelta(days=days - 1)
    groups: dict[date, list[Entry]] = {}
    for e in calendar_entries(start, end, today=today, can_write=can_write):
        groups.setdefault(max(e.start, start), []).append(e)
    return [(d, groups[d]) for d in sorted(groups)]


def overdue_tasks(today: date, limit: int | None = None) -> tuple[int, list[TaskOccurrence]]:
    qs = (TaskOccurrence.objects.filter(status=OPEN, due_date__lt=today, task__archived_at__isnull=True)
          .select_related("task__property", "task__item").order_by("due_date", "id"))
    return qs.count(), list(qs[:limit] if limit else qs)


# ---- dashboard -----------------------------------------------------------------------------------
@dataclass
class Dashboard:
    overdue_count: int = 0
    overdue: list = field(default_factory=list)
    overdue_more: int = 0
    due_week_count: int = 0
    due_week: list = field(default_factory=list)
    due_month_count: int = 0
    open_count: int = 0
    warranties_expiring: list = field(default_factory=list)
    warranties_expired: list = field(default_factory=list)
    events_upcoming: list = field(default_factory=list)
    attention_count: int = 0
    attention: list = field(default_factory=list)
    spend_total: object = 0
    spend_jobs: int = 0
    spend_by_property: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)


def dashboard(today: date, *, can_write: bool, limit: int = 6) -> Dashboard:
    d = Dashboard()
    open_work = TaskOccurrence.objects.filter(status=OPEN, task__archived_at__isnull=True)

    d.overdue_count, d.overdue = overdue_tasks(today, limit)
    d.overdue_more = d.overdue_count - len(d.overdue)
    week = open_work.filter(due_date__range=(today, today + timedelta(days=7)))
    d.due_week_count = week.count()
    d.due_week = list(week.select_related("task__property", "task__item").order_by("due_date", "id")[:limit])
    d.due_month_count = open_work.filter(due_date__range=(today, today + timedelta(days=30))).count()
    d.open_count = open_work.count()

    items = InventoryItem.objects.active().select_related("property", "unit")
    d.warranties_expiring = list(items.filter(
        warranty_expires_on__range=(today, today + timedelta(days=WARRANTY_WINDOW_DAYS)))
        .order_by("warranty_expires_on", "id")[:limit])
    d.warranties_expired = list(items.filter(
        warranty_expires_on__range=(today - timedelta(days=RECENTLY_EXPIRED_DAYS), today - timedelta(days=1)))
        .order_by("-warranty_expires_on", "id")[:limit])
    attention = items.filter(condition__in=[InventoryItem.Condition.NEEDS_REPLACEMENT, InventoryItem.Condition.POOR])
    d.attention_count = attention.count()
    # "needs replacement" before "poor"; ordering by the stored code is not alphabetical-safe, so be explicit.
    d.attention = sorted(attention[:50], key=lambda i: (i.condition != "replace", i.property.name, i.name))[:limit]

    d.events_upcoming = list(Event.objects.active().select_related("property")
                             .filter(Q(starts_on__range=(today, today + timedelta(days=14)))
                                     | Q(starts_on__lt=today, ends_on__gte=today))
                             .order_by("starts_on", "start_time", "id")[:limit])

    since = today - timedelta(days=SPEND_WINDOW_DAYS - 1)
    spent = TaskOccurrence.objects.filter(status=DONE, cost__isnull=False, completed_on__range=(since, today))
    totals = spent.aggregate(total=Sum("cost"), jobs=Count("id"))
    d.spend_total, d.spend_jobs = totals["total"] or 0, totals["jobs"]
    d.spend_by_property = list(spent.values("task__property__name")
                               .annotate(total=Sum("cost"), jobs=Count("id"))
                               .order_by("-total", "task__property__name")[:5])

    d.counts = {"Properties": (Property.objects.active().count(), "property_list"),
                "Units": (Unit.objects.active().count(), "unit_list"),
                "Inventory items": (items.count(), "inventoryitem_list"),
                "Contacts": (Contact.objects.active().count(), "contact_list")}
    return d
