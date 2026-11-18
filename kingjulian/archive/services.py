"""Rules for bringing a record back."""
import re
from datetime import date, datetime, time
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils.text import capfirst

from core.models import TenantModel

from .registry import BY_MODEL, kind_of

HIDDEN_FIELDS = {"id", "account", "created_at", "updated_at", "archived_at"}


class RestoreBlocked(Exception):
    """The record can't come back yet; `.reasons` are sentences fit to show the user."""

    def __init__(self, reasons):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def blockers(obj):
    """Archived records this one points at. Restoring a child while its parent is archived would leave a visible
    record pointing at something hidden (and its edit form would silently drop that link)."""
    found = []
    for f in obj._meta.concrete_fields:
        if f.many_to_one and f.related_model and issubclass(f.related_model, TenantModel) and getattr(obj, f.attname) is not None:
            related = getattr(obj, f.name)
            if related.archived_at is not None:
                found.append(related)
    return found


def describe_blocker(related):
    kind = BY_MODEL.get(type(related))
    label = kind.label.lower() if kind else related._meta.verbose_name
    name = kind.name(related) if kind else str(related)
    return f"Restore the {label} “{name}” first."


def restore_record(obj):
    """Restore `obj` (audited by the normal save signals). Raises RestoreBlocked with user-facing reasons."""
    if obj.archived_at is None:
        return False                                    # already active: nothing to do
    pending = blockers(obj)
    if pending:
        raise RestoreBlocked([describe_blocker(r) for r in pending])
    try:
        with transaction.atomic():
            obj.restore()
    except IntegrityError:
        raise RestoreBlocked(["An active record already uses the same name here. Rename or archive that one first."])
    return True


def readable_label(f):
    """'Address line1' -> 'Address line 1'."""
    return re.sub(r"(?<=[a-z])(\d)$", r" \1", capfirst(str(f.verbose_name)))


def readable_value(obj, f, value):
    if value is None or value == "":
        return ""
    if f.choices:
        return dict(f.flatchoices).get(value, str(value))
    if f.many_to_one:
        return str(getattr(obj, f.name))
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, datetime):
        return f"{value:%b %-d, %Y %-I:%M %p}"
    if isinstance(value, date):
        return f"{value:%b %-d, %Y}"
    if isinstance(value, time):
        return f"{value:%-I:%M %p}"
    if isinstance(value, Decimal) and f.get_internal_type() == "DecimalField":
        return f"${value:,.2f}"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)


def describe_fields(obj):
    """[(label, value)] for a read-only view: choice labels, related names, friendly dates and money, no internal columns."""
    kind = kind_of(obj)
    skip = HIDDEN_FIELDS | (kind.skip_fields if kind else frozenset())
    rows = [(readable_label(f), readable_value(obj, f, getattr(obj, f.attname)))
            for f in obj._meta.concrete_fields if f.name not in skip]
    if kind:
        rows += kind.extra(obj)
    return rows
