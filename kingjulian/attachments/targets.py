"""Which objects can carry files, and the FK column each one uses on Attachment."""
from contacts.models import Contact
from inventory.models import InventoryItem
from maintenance.models import MaintenanceTask, TaskOccurrence
from planner.models import Event
from properties.models import Property

TARGETS = {                       # url kind -> (model, Attachment field)
    "property": (Property, "property"),
    "item": (InventoryItem, "item"),
    "task": (MaintenanceTask, "task"),
    "occurrence": (TaskOccurrence, "occurrence"),
    "contact": (Contact, "contact"),
    "event": (Event, "event"),
}
FIELDS = tuple(field for _, field in TARGETS.values())
KIND_BY_MODEL = {model: kind for kind, (model, _) in TARGETS.items()}
EDIT_URL_NAME = {"property": "property_edit", "item": "inventoryitem_edit", "task": "task_edit",
                 "occurrence": "occurrence_edit", "contact": "contact_edit", "event": "event_edit"}


def kind_for(obj):
    return KIND_BY_MODEL.get(type(obj))
