"""Demo maintenance tasks for the accounts created by seed_demo. Safe to re-run."""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import Account
from contacts.models import Contact
from core.tenancy import use_account
from inventory.models import InventoryItem
from maintenance.models import MaintenanceTask
from maintenance.services import ensure_first_occurrence
from properties.models import Property

FIXED, AFTER = MaintenanceTask.ScheduleMode.FIXED, MaintenanceTask.ScheduleMode.AFTER_COMPLETION

# (title, property, item name or None, rule, mode, start offset in days, contact, remind days, description)
SUNRISE = [
    ("Annual boiler service", "Maple Street Fourplex", "Lochinvar Boiler", "FREQ=YEARLY", FIXED, 12,
     "CoolAir HVAC Service", [14, 7, 1], "Combustion test, heat exchanger inspection, pressure relief valve check."),
    ("Replace smoke detector batteries", "Maple Street Fourplex", "Smoke Detector (hardwired)", "FREQ=MONTHLY;INTERVAL=6",
     FIXED, -10, None, [7, 1], "All units. Test each detector after replacing."),
    ("Rooftop HVAC filter change", "Riverside Plaza Suite 200", "Rooftop HVAC Unit 5-ton", "FREQ=MONTHLY;INTERVAL=3",
     AFTER, 3, "CoolAir HVAC Service", [7, 1], "MERV 13, four filters."),
    ("Test sump pumps", "Oak Hollow Cottage", "Sump Pump", "FREQ=MONTHLY", FIXED, 1, None, [1, 0],
     "Pour a bucket into the pit; confirm both pumps start."),
    ("Fire alarm annual inspection", "Riverside Plaza Suite 200", "Fire Alarm Panel", "", FIXED, 45,
     "Marcus Volt", [30, 7], "One-off; schedule the next one after the report."),
    ("Flush water heater", "Maple Street Fourplex", "Rheem 75-gal Water Heater", "FREQ=YEARLY", FIXED, 90,
     "Dana Pipes", [7, 1], ""),
]
LAKESIDE = [
    ("Boiler inspection (Lakeside)", "Lakeview Tower", "Lakeside Boiler", "FREQ=YEARLY", FIXED, 2, None, [7, 1], ""),
]


class Command(BaseCommand):
    help = "Create demo maintenance tasks (needs seed_demo first)."

    def handle(self, *args, **opts):
        today = timezone.localdate()
        with transaction.atomic():
            for name, specs in (("Sunrise Rentals", SUNRISE), ("Lakeside Holdings", LAKESIDE)):
                account = Account.objects.get(name=name)
                with use_account(account):
                    if MaintenanceTask.objects.exists():
                        self.stdout.write(f"{name}: tasks already exist, skipping")
                        continue
                    for title, prop, item, rule, mode, offset, contact, remind, desc in specs:
                        p = Property.objects.get(name=prop)
                        task = MaintenanceTask.objects.create(
                            property=p, unit=None, item=InventoryItem.objects.get(property=p, name=item) if item else None,
                            assigned_contact=Contact.objects.get(name=contact) if contact else None,
                            title=title, description=desc, start_date=today + timedelta(days=offset),
                            recurrence_rule=rule, schedule_mode=mode, remind_days_before=remind)
                        ensure_first_occurrence(task)
                    self.stdout.write(self.style.SUCCESS(f"{name}: {len(specs)} tasks created"))
