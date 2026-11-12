"""CSV datasets: one definition per list, used by both the per-list 'Export CSV' button and the Export page."""
from dataclasses import dataclass, field
from typing import Callable

from django.db.models import OuterRef, Subquery

from attachments.models import Attachment
from audit.models import ActivityLog
from contacts.models import Contact
from inventory.models import InventoryItem
from maintenance.models import MaintenanceTask, TaskOccurrence
from planner.models import Event
from properties.models import Property, Unit


def path(dotted: str):
    """Getter for 'task.property.name' that yields None instead of failing on a missing link; calls callables."""
    parts = dotted.split(".")

    def get(obj):
        for p in parts:
            if obj is None:
                return None
            obj = getattr(obj, p, None)
            if callable(obj):
                obj = obj()
        return obj
    return get


def archived(obj):
    return obj.archived_at


@dataclass(frozen=True)
class Dataset:
    key: str
    title: str
    model: type
    columns: list
    select_related: tuple = ()
    prefetch: tuple = ()
    prepare: Callable = lambda qs: qs
    order_by: tuple = ()
    description: str = ""

    def queryset(self, base=None):
        """`base` is a (possibly filtered) tenant-scoped queryset; default is every row, archived included."""
        qs = base if base is not None else self.model.objects.all()
        qs = self.prepare(qs)
        if self.select_related:
            qs = qs.select_related(*self.select_related)
        if self.prefetch:
            qs = qs.prefetch_related(*self.prefetch)
        return qs.order_by(*self.order_by) if self.order_by else qs


def _tasks_prepare(qs):
    if "next_due" in qs.query.annotations:
        return qs
    nxt = TaskOccurrence.objects.filter(task=OuterRef("pk"), status="open").values("due_date")[:1]
    return qs.annotate(next_due=Subquery(nxt))


def _contact_properties(c):
    return "; ".join(p.name for p in c.properties.all())


DATASETS = {d.key: d for d in [
    Dataset("properties", "Properties", Property, [
        ("Name", path("name")), ("Type", path("get_property_type_display")), ("Address line 1", path("address_line1")),
        ("Address line 2", path("address_line2")), ("City", path("city")), ("State", path("state")),
        ("Postal code", path("postal_code")), ("Notes", path("notes")), ("Archived", archived)], order_by=("name",)),
    Dataset("units", "Units", Unit, [
        ("Property", path("property.name")), ("Unit", path("name")), ("Notes", path("notes")), ("Archived", archived)],
        select_related=("property",), order_by=("property__name", "name")),
    Dataset("inventory", "Inventory", InventoryItem, [
        ("Item", path("name")), ("Property", path("property.name")), ("Unit", path("unit.name")),
        ("Category", path("get_category_display")), ("Quantity", path("quantity")), ("Serial number", path("serial_number")),
        ("Purchase date", path("purchase_date")), ("Cost", path("cost")), ("Condition", path("get_condition_display")),
        ("Warranty expires", path("warranty_expires_on")), ("Notes", path("notes")), ("Archived", archived)],
        select_related=("property", "unit"), order_by=("property__name", "name")),
    Dataset("contacts", "Contacts", Contact, [
        ("Name", path("name")), ("Role", path("get_role_display")), ("Company", path("company")), ("Phone", path("phone")),
        ("Email", path("email")), ("Properties", _contact_properties), ("Notes", path("notes")), ("Archived", archived)],
        prefetch=("properties",), order_by=("name",)),
    Dataset("tasks", "Maintenance tasks", MaintenanceTask, [
        ("Task", path("title")), ("Property", path("property.name")), ("Unit", path("unit.name")), ("Item", path("item.name")),
        ("Assigned to", path("assigned_contact.name")), ("Start date", path("start_date")), ("Repeats", path("schedule_label")),
        ("Reminder days before", lambda t: ", ".join(str(d) for d in t.remind_days_before)),
        ("Next due", path("next_due")), ("Description", path("description")), ("Archived", archived)],
        select_related=("property", "unit", "item", "assigned_contact"), prepare=_tasks_prepare, order_by=("title",)),
    Dataset("history", "Maintenance history", TaskOccurrence, [
        ("Task", path("task.title")), ("Property", path("task.property.name")), ("Item", path("task.item.name")),
        ("Due date", path("due_date")), ("Status", path("get_status_display")), ("Completed on", path("completed_on")),
        ("Completed by", path("completed_by.email")), ("Cost", path("cost")), ("Notes", path("notes"))],
        select_related=("task__property", "task__item", "completed_by"), order_by=("-due_date", "-id")),
    Dataset("events", "Events", Event, [
        ("Event", path("title")), ("Type", path("get_kind_display")), ("Date", path("starts_on")), ("Last day", path("ends_on")),
        ("Start time", path("start_time")), ("End time", path("end_time")), ("Property", path("property.name")),
        ("With", path("contact.name")), ("Notes", path("notes")), ("Archived", archived)],
        select_related=("property", "contact"), order_by=("starts_on", "start_time", "id")),
    Dataset("attachments", "Files (list only)", Attachment, [
        ("File", path("original_name")), ("Attached to", path("target_type_label")), ("Record", path("target_label")),
        ("Size (bytes)", path("size")), ("Type", path("content_type")), ("Caption", path("caption")),
        ("Uploaded by", path("uploader_label")), ("Uploaded", path("created_at")), ("SHA-256", path("sha256"))],
        select_related=("uploaded_by", "property", "item", "task", "occurrence__task", "contact", "event"),
        order_by=("-created_at",), description="Metadata only; the files themselves are not included."),
    Dataset("activity", "Activity log", ActivityLog, [
        ("When", path("created_at")), ("Who", path("actor_label")), ("Action", path("get_action_display")),
        ("Object type", path("target_type")), ("Object", path("target_label")), ("Summary", path("summary")),
        ("Changes", path("changes_label"))], select_related=("actor",), order_by=("-created_at", "-id")),
]}
