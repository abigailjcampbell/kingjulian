"""Which record types can be archived, and how each one is described on the Archive page."""
from dataclasses import dataclass, field
from typing import Callable

from django.urls import reverse

from contacts.models import Contact
from inventory.models import InventoryItem
from maintenance.models import MaintenanceTask
from planner.models import Event
from properties.models import Property, Unit


@dataclass(frozen=True)
class Kind:
    key: str                        # used in URLs: /archive/<key>/<pk>/
    model: type
    label: str                      # "Inventory item"
    name: Callable                  # obj -> the record's own name
    where: Callable                 # obj -> short context ("Maple Duplex")
    search: tuple                   # ORM lookups searched by ?q=
    select_related: tuple = ()
    skip_fields: frozenset = frozenset()                  # fields replaced by a friendlier "extra" row
    extra: Callable = lambda obj: []                      # obj -> [(label, value), ...]


def _contact_properties(c):
    return [("Properties", "; ".join(p.name for p in c.properties.all()))]


KINDS = {k.key: k for k in [
    Kind("property", Property, "Property", lambda o: o.name, lambda o: o.city, ("name", "city", "address_line1", "notes")),
    Kind("unit", Unit, "Unit", lambda o: o.name, lambda o: o.property.name, ("name", "property__name", "notes"), ("property",)),
    Kind("item", InventoryItem, "Inventory item", lambda o: o.name, lambda o: o.property.name,
         ("name", "serial_number", "property__name", "notes"), ("property",)),
    Kind("contact", Contact, "Contact", lambda o: o.name, lambda o: o.company or o.get_role_display(),
         ("name", "company", "email", "phone", "notes"), extra=_contact_properties),
    Kind("task", MaintenanceTask, "Maintenance task", lambda o: o.title, lambda o: o.property.name,
         ("title", "description", "property__name"), ("property",),
         skip_fields=frozenset({"recurrence_rule", "schedule_mode"}), extra=lambda t: [("Repeats", t.schedule_label)]),
    Kind("event", Event, "Event", lambda o: o.title, lambda o: o.property.name if o.property else "",
         ("title", "notes", "property__name"), ("property",)),
]}
BY_MODEL = {k.model: k for k in KINDS.values()}


def kind_of(obj):
    return BY_MODEL.get(type(obj))


def detail_url(obj):
    """URL of the read-only archived view for a record, or None if its type is not archivable."""
    kind = kind_of(obj)
    return reverse("archive_detail", args=[kind.key, obj.pk]) if kind else None
