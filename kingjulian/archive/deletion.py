"""Permanent deletion of archived records.

Only records that are already archived can be deleted (archive first, then delete), and the rules below decide what a
deletion takes with it. The database's PROTECT rules are a second line of defence: a code path that forgets a child
record fails with an error instead of orphaning or silently destroying data.

  property   owns its files; links to contacts are removed; BLOCKED while any unit, item, task or event still belongs to it
  unit       BLOCKED while any item or task is in it
  item       owns its files; BLOCKED while any maintenance task uses it
  contact    owns its files; links removed; tasks/events it was assigned to are kept and simply lose the contact
  task       owns ALL its jobs (history and costs), their reminders and files, and its own files
  event      owns its files
"""
import hashlib
import json
from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Q, Sum
from django.urls import reverse

from attachments.models import Attachment
from attachments.services import delete_attachment
from attachments.validators import human_size
from audit.context import quiet
from audit.models import ActivityLog
from audit.signals import record
from contacts.models import ContactProperty
from inventory.models import InventoryItem
from maintenance.models import Reminder, TaskOccurrence, MaintenanceTask
from planner.models import Event
from properties.models import Unit

from .registry import BY_MODEL, kind_of

CONFIRM_WORD = "DELETE"
LIVE_EDIT = {"property": "property_detail", "unit": "unit_detail", "item": "inventoryitem_detail", "contact": "contact_detail",
             "task": "task_detail", "event": "event_detail"}          # a live record's read-only page

# kind -> [(label, model, field on that model pointing back at us)]
BLOCKED_BY = {
    "property": [("unit", Unit, "property"), ("inventory item", InventoryItem, "property"),
                 ("maintenance task", MaintenanceTask, "property"), ("event", Event, "property")],
    "unit": [("inventory item", InventoryItem, "unit"), ("maintenance task", MaintenanceTask, "unit")],
    "item": [("maintenance task", MaintenanceTask, "item")],
}


class NotArchived(Exception):
    """Only archived records may be deleted."""


class DeleteBlocked(Exception):
    def __init__(self, plan):
        super().__init__("blocked")
        self.plan = plan


@dataclass
class Blocker:
    label: str                      # "inventory item"
    count: int
    examples: list                  # [(name, url)] up to 5, linking to where to deal with each


@dataclass
class Plan:
    blockers: list = field(default_factory=list)
    deletes: list = field(default_factory=list)          # sentences: what will be destroyed along with the record
    unlinks: list = field(default_factory=list)          # sentences: what will merely be changed
    file_names: list = field(default_factory=list)
    token: str = ""

    @property
    def files_label(self):
        """'a.pdf, b.png' (first ten, then 'and N more'): shown on the confirmation page and in the activity log."""
        shown = ", ".join(self.file_names[:10])
        return shown + (f" and {len(self.file_names) - 10} more" if len(self.file_names) > 10 else "")


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def _where(obj):
    """Where to go to deal with an object that is in the way: its archive page if archived, else its live page."""
    kind = kind_of(obj)
    if not kind:
        return None
    if obj.archived_at is not None:
        return reverse("archive_detail", args=[kind.key, obj.pk])
    return reverse(LIVE_EDIT[kind.key], args=[obj.pk])


def attachments_of(obj):
    kind = kind_of(obj).key
    if kind == "unit":
        return Attachment.objects.none()
    if kind == "task":
        return Attachment.objects.filter(Q(task=obj) | Q(occurrence__task=obj))
    return Attachment.objects.filter(**{kind: obj})


def build_plan(obj) -> Plan:
    kind = kind_of(obj)
    plan = Plan()
    facts = {"kind": kind.key, "pk": obj.pk}

    for label, model, fk in BLOCKED_BY.get(kind.key, []):
        qs = model.objects.filter(**{fk: obj})
        n = qs.count()
        if n:
            k = BY_MODEL[model]
            plan.blockers.append(Blocker(label, n, [(k.name(o), _where(o)) for o in qs.select_related()[:5]]))
            facts[f"blocked_{label}"] = n

    files = list(attachments_of(obj).order_by("id"))
    if files:
        plan.deletes.append(f"{plural(len(files), 'file')} ({human_size(sum(a.size for a in files))})")
        plan.file_names = [a.original_name for a in files]
        facts["files"] = [a.pk for a in files]

    if kind.key == "task":
        occ = TaskOccurrence.objects.filter(task=obj)
        n = occ.count()
        if n:
            done = occ.filter(status="done")
            cost = done.aggregate(total=Sum("cost"))["total"]
            text = f"{plural(n, 'job')} of history ({done.count()} completed"
            text += f", ${cost:,.2f} of recorded cost)" if cost else ")"
            plan.deletes.append(text)
            reminders = Reminder.objects.filter(occurrence__task=obj).count()
            if reminders:
                plan.deletes.append(plural(reminders, "reminder record"))
            facts["jobs"] = n
            facts["reminders"] = reminders

    if kind.key == "contact":
        tasks = MaintenanceTask.objects.filter(assigned_contact=obj).count()
        events = Event.objects.filter(contact=obj).count()
        if tasks:
            plan.unlinks.append(f"{plural(tasks, 'maintenance task')} will keep existing but no longer have this contact assigned")
        if events:
            plan.unlinks.append(f"{plural(events, 'event')} will keep existing but no longer list this contact")
        facts.update(tasks=tasks, events=events)
    if kind.key in ("contact", "property"):
        links = ContactProperty.objects.filter(**{kind.key: obj}).count()
        if links:
            plan.unlinks.append(f"{plural(links, 'contact-to-property link')} will be removed")
        facts["links"] = links

    plan.token = hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()[:24]
    return plan


def delete_forever(obj) -> Plan:
    """Delete an archived record and everything it owns, in one transaction, writing ONE activity entry for it all.

    Files leave the disk only after the transaction commits. Raises NotArchived or DeleteBlocked (nothing is changed)."""
    kind = kind_of(obj)
    with transaction.atomic():
        locked = kind.model.objects.select_for_update().get(pk=obj.pk)        # serialise concurrent deletes
        if locked.archived_at is None:
            raise NotArchived()
        plan = build_plan(locked)
        if plan.blockers:
            raise DeleteBlocked(plan)
        name, pk, account_id, model_name = kind.name(locked), locked.pk, locked.account_id, locked._meta.model_name
        with quiet():                                                           # the summary entry below replaces per-row noise
            for att in list(attachments_of(locked)):
                delete_attachment(att)
            if kind.key == "task":
                TaskOccurrence.objects.filter(task=locked).delete()             # reminders go with them (CASCADE)
            locked.delete()
        changes = {}
        if plan.deletes:
            changes["Also deleted"] = ["", "; ".join(plan.deletes)]
        if plan.file_names:
            changes["Files"] = ["", plan.files_label]
        if plan.unlinks:
            changes["Also changed"] = ["", "; ".join(plan.unlinks)]
        record(account_id=account_id, action=ActivityLog.Action.DELETED,
               summary=f"Permanently deleted {kind.label.lower()} “{name}”", target=(model_name, pk, name), changes=changes)
    return plan
