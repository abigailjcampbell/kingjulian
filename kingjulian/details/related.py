"""What else is worth seeing on a record's page: the things that belong to it or point at it."""
from dataclasses import dataclass, field

from django.db.models import Count, OuterRef, Subquery, Sum
from django.urls import reverse

from contacts.models import Contact
from inventory.models import InventoryItem
from maintenance.models import MaintenanceTask, TaskOccurrence
from planner.models import Event
from properties.models import Unit

from .registry import record_url

LIMIT = 8


@dataclass
class Row:
    text: str
    url: str | None = None
    sub: str = ""


@dataclass
class Section:
    title: str
    rows: list = field(default_factory=list)
    total: int = 0
    more_url: str | None = None
    add_url: str | None = None           # "+ Add" link shown in the heading (writers only)
    add_label: str = ""
    empty_text: str = ""                 # shown instead of the section being hidden, when add_url is set


def _section(title, qs, make_row, more=None, add=None):
    """more=(url name, query string[, reverse args]); add=(url, label, empty text) keeps the section visible when empty."""
    total = qs.count()
    if not total and not add:
        return None
    more_url = None
    if more and total > LIMIT:
        more_url = reverse(more[0], args=more[2] if len(more) > 2 else None) + (("?" + more[1]) if more[1] else "")
    section = Section(title, [make_row(o) for o in qs[:LIMIT]], total, more_url)
    if add:
        section.add_url, section.add_label, section.empty_text = add
    return section


def _unit_row(u):
    return Row(u.name, record_url(u))


def _item_row(i):
    bits = [i.get_category_display(), i.get_condition_display()]
    if i.unit_id:
        bits.insert(0, i.unit.name)
    return Row(i.name, record_url(i), " · ".join(bits))


def _task_row(t):
    nxt = getattr(t, "next_due", None)
    return Row(t.title, record_url(t), t.schedule_label + (f" · next due {nxt:%b %-d, %Y}" if nxt else ""))


def _contact_row(c):
    return Row(c.name, record_url(c), c.company or c.get_role_display())


def _event_row(e):
    return Row(e.title, record_url(e), f"{e.date_label} · {e.time_label}")


def _job_row(o):
    bits = [o.get_status_display()]
    if o.completed_on:
        bits.append(f"completed {o.completed_on:%b %-d, %Y}")
    if o.cost is not None:
        bits.append(f"${o.cost:,.2f}")
    return Row(f"{o.due_date:%b %-d, %Y}", reverse("occurrence_edit", args=[o.pk]), " · ".join(bits))


def _tasks(**where):
    nxt = TaskOccurrence.objects.filter(task=OuterRef("pk"), status="open").values("due_date")[:1]
    return (MaintenanceTask.objects.active().filter(**where).annotate(next_due=Subquery(nxt))
            .order_by("title"))


def sections_for(key, obj, can_write=False):
    """Non-empty sections for this record, in reading order. A property's Units section stays visible (with an Add button) for writers."""
    pk = obj.pk
    if key == "property":
        found = [
            _section("Units", Unit.objects.active().filter(property=obj).order_by("name"), _unit_row, ("property_units", "", [pk]),
                     add=(f"{reverse('unit_create')}?property={pk}", "+ Add unit", "No units yet.") if can_write else None),
            _section("Inventory", InventoryItem.objects.active().filter(property=obj).select_related("unit").order_by("name"), _item_row,
                     ("inventoryitem_list", f"property={pk}")),
            _section("Maintenance tasks", _tasks(property=obj), _task_row, ("task_list", f"property={pk}")),
            _section("Contacts", obj.contacts.filter(archived_at__isnull=True).order_by("name"), _contact_row),
            _section("Events", Event.objects.active().filter(property=obj).order_by("-starts_on", "-id"), _event_row,
                     ("event_list", f"property={pk}&when=")),
        ]
    elif key == "unit":
        found = [
            _section("Inventory in this unit", InventoryItem.objects.active().filter(unit=obj).order_by("name"), _item_row),
            _section("Maintenance tasks", _tasks(unit=obj), _task_row),
        ]
    elif key == "item":
        found = [_section("Maintenance tasks", _tasks(item=obj), _task_row)]
    elif key == "contact":
        found = [
            _section("Properties", obj.properties.filter(archived_at__isnull=True).order_by("name"),
                     lambda p: Row(p.name, record_url(p), p.city)),
            _section("Assigned maintenance tasks", _tasks(assigned_contact=obj), _task_row),
            _section("Events", Event.objects.active().filter(contact=obj).order_by("-starts_on", "-id"), _event_row),
        ]
    elif key == "task":
        found = [_section("Jobs", TaskOccurrence.objects.filter(task=obj).order_by("-due_date", "-id"), _job_row)]
    else:
        found = []
    return [s for s in found if s]


def extra_rows(key, obj, today):
    """Summary rows computed from related data (appended to the field table)."""
    rows = []
    if key == "task":
        nxt = TaskOccurrence.objects.filter(task=obj, status="open").order_by("due_date").first()
        done = TaskOccurrence.objects.filter(task=obj, status="done").aggregate(n=Count("id"), total=Sum("cost"))
        rows.append(("Next due", f"{nxt.due_date:%b %-d, %Y} ({nxt.due_label})" if nxt else "Nothing scheduled"))
        rows.append(("Completed jobs", str(done["n"])))
        if done["total"]:
            rows.append(("Total recorded cost", f"${done['total']:,.2f}"))
    elif key == "item" and obj.warranty_expires_on:
        days = (obj.warranty_expires_on - today).days
        text = ("Expires today" if days == 0 else f"Expires in {days} day{'s' if days != 1 else ''}" if days > 0
                else f"Expired {-days} day{'s' if days != -1 else ''} ago")
        rows.append(("Warranty status", text))
    return rows
