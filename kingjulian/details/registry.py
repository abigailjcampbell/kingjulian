"""URL names for each record type's pages. The read-only page is where clicking an item lands; edit is a button on it."""
from django.urls import reverse

from archive.registry import KINDS, kind_of

DETAIL_NAMES = {"property": "property_detail", "unit": "unit_detail", "item": "inventoryitem_detail",
                "contact": "contact_detail", "task": "task_detail", "event": "event_detail"}
EDIT_NAMES = {"property": "property_edit", "unit": "unit_edit", "item": "inventoryitem_edit",
              "contact": "contact_edit", "task": "task_edit", "event": "event_edit"}
ARCHIVE_NAMES = {"property": "property_archive", "unit": "unit_archive", "item": "inventoryitem_archive",
                 "contact": "contact_archive", "task": "task_archive", "event": "event_archive"}
LIST_NAMES = {"property": "property_list", "unit": "unit_list", "item": "inventoryitem_list",
              "contact": "contact_list", "task": "task_list", "event": "event_list"}
LIST_TITLES = {"property": "Properties", "unit": "Units", "item": "Inventory", "contact": "Contacts",
               "task": "Maintenance tasks", "event": "Events"}


def record_url(obj):
    """The read-only page of a live record, or None for types that don't have one (or if it is archived)."""
    kind = kind_of(obj)
    if not kind or getattr(obj, "archived_at", None) is not None:
        return None
    return reverse(DETAIL_NAMES[kind.key], args=[obj.pk])
