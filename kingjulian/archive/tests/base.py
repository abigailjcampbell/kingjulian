from datetime import date as D

from audit.context import acting_as
from attachments.tests.base import FilesWorld
from contacts.models import Contact
from core.tenancy import use_account
from inventory.models import InventoryItem
from maintenance.models import MaintenanceTask
from planner.models import Event
from properties.models import Property, Unit


class ArchiveWorld(FilesWorld):
    def archive(self, obj, user=None, account=None):
        """Archive as `user` (None = the system, i.e. no signed-in actor)."""
        with use_account(account or self.acme), acting_as(user):
            type(obj).objects.get(pk=obj.pk).archive()

    def make_all(self):
        """One active record of each archivable type, all hanging off a new property."""
        with use_account(self.acme):
            prop = Property.objects.create(name="Birch Lodge", city="Ogdenville", notes="Corner lot")
            unit = Unit.objects.create(property=prop, name="Cellar")
            item = InventoryItem.objects.create(property=prop, unit=unit, name="Sump Pump", serial_number="SP-1", category="plumbing")
            contact = Contact.objects.create(name="Zed Roofer", role="contractor", company="Roofers Inc")
            task = MaintenanceTask.objects.create(property=prop, title="Check sump", start_date=D(2026, 12, 1), recurrence_rule="FREQ=YEARLY")
            event = Event.objects.create(title="Walkthrough", starts_on=D(2026, 12, 5), property=prop)
        return {"property": prop, "unit": unit, "item": item, "contact": contact, "task": task, "event": event}

    def fresh(self, obj, account=None):
        with use_account(account or self.acme):
            return type(obj).objects.get(pk=obj.pk)

    @staticmethod
    def snapshot(obj):
        """Every stored value except the two bookkeeping columns that legitimately change."""
        return {f.attname: getattr(obj, f.attname) for f in obj._meta.concrete_fields if f.name not in ("updated_at", "archived_at")}
