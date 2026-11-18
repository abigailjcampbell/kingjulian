"""Rules for bringing a record back."""
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


def describe_fields(obj):
    """[(label, value)] for a read-only view: choice labels, related names, no internal columns."""
    kind = kind_of(obj)
    skip = HIDDEN_FIELDS | (kind.skip_fields if kind else frozenset())
    rows = []
    for f in obj._meta.concrete_fields:
        if f.name in skip:
            continue
        value = getattr(obj, f.attname)
        if value is None or value == "":
            shown = ""
        elif f.choices:
            shown = dict(f.flatchoices).get(value, str(value))
        elif f.many_to_one:
            shown = str(getattr(obj, f.name))
        elif isinstance(value, bool):
            shown = "Yes" if value else "No"
        elif isinstance(value, (list, tuple)):
            shown = ", ".join(str(v) for v in value)
        else:
            shown = str(value)
        rows.append((capfirst(str(f.verbose_name)), shown))
    if kind:
        rows += kind.extra(obj)
    return rows
