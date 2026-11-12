"""What gets audited, and how each model is described in the log."""
import json
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import models as dj_models

from attachments.models import Attachment
from contacts.models import Contact, ContactProperty
from inventory.models import InventoryItem
from maintenance.models import MaintenanceTask, TaskOccurrence
from planner.models import Event
from properties.models import Property, Unit

BASE_IGNORE = {"id", "account", "created_at", "updated_at", "archived_at"}
TEXT_LIMIT = 200


@dataclass
class Spec:
    model: type
    track_create: bool = True
    ignore: set = field(default_factory=set)
    create_action: str = "created"
    delete_action: str = "deleted"


SPECS = {s.model: s for s in [
    Spec(Property), Spec(Unit), Spec(Contact), Spec(InventoryItem), Spec(MaintenanceTask), Spec(Event),
    # Occurrences are created by the scheduler; only human-driven changes are interesting.
    Spec(TaskOccurrence, track_create=False),
    Spec(Attachment, create_action="uploaded", ignore={"file", "original_name", "content_type", "size", "sha256", "uploaded_by"}),
    Spec(ContactProperty, create_action="linked", delete_action="unlinked"),
]}


def tracked_fields(spec: Spec):
    skip = BASE_IGNORE | spec.ignore
    return [f for f in spec.model._meta.concrete_fields if f.name not in skip]


def describe_target(instance):
    """(type, id, label) of the thing this entry is *about*. Files and links belong to their parent."""
    if isinstance(instance, Attachment):
        t = instance.target
        return t._meta.model_name, t.pk, instance.target_label
    if isinstance(instance, ContactProperty):
        return "contact", instance.contact_id, instance.contact.name
    if isinstance(instance, TaskOccurrence):
        return "taskoccurrence", instance.pk, instance.task.title        # str() adds the date; the summary shows it once
    return instance._meta.model_name, instance.pk, str(instance)


def verbose(instance) -> str:
    if isinstance(instance, TaskOccurrence):
        return "maintenance job"
    return instance._meta.verbose_name


def _clip(text: str) -> str:
    return text if len(text) <= TEXT_LIMIT else text[: TEXT_LIMIT - 1] + "…"


def _label_for_fk(field, value) -> str:
    related = field.related_model
    manager = getattr(related, "all_tenants", None) or related._default_manager     # User has no all_tenants
    obj = manager.filter(pk=value).first()
    return str(obj) if obj else f"#{value}"


def display(field, value) -> str:
    """Human-readable form of a raw column value, for the 'Changes' column.

    Purely cosmetic: if anything about rendering a label goes wrong we fall back to the raw value, because a
    display problem must never stop someone's save. (Failing to *write* the log row still fails loudly.)
    """
    try:
        if value is None or value == "":
            return ""
        if field.choices:
            return dict(field.flatchoices).get(value, str(value))
        if field.many_to_one:
            return _label_for_fk(field, value)
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, Decimal):
            return f"{value:.2f}" if field.get_internal_type() == "DecimalField" else str(value)
        if isinstance(value, (list, dict)):
            return _clip(json.dumps(value))
        return _clip(str(value))
    except Exception:                                    # noqa: BLE001
        return _clip(str(value))
