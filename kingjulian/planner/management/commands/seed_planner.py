"""Demo events, maintenance history (for the spend panel) and two warranty tweaks. Safe to re-run.

Run after seed_demo and seed_maintenance.
"""
from datetime import time, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import Account
from contacts.models import Contact
from core.tenancy import use_account
from inventory.models import InventoryItem
from maintenance.models import MaintenanceTask, TaskOccurrence
from planner.models import Event
from properties.models import Property

K = Event.Kind


class Command(BaseCommand):
    help = "Create demo calendar events and maintenance history."

    def handle(self, *args, **opts):
        today = timezone.localdate()
        d = lambda n: today + timedelta(days=n)
        with transaction.atomic():
            sunrise = Account.objects.get(name="Sunrise Rentals")
            with use_account(sunrise):
                if Event.objects.exists():
                    self.stdout.write("Sunrise Rentals: events already exist, skipping")
                else:
                    maple, oak = Property.objects.get(name="Maple Street Fourplex"), Property.objects.get(name="Oak Hollow Cottage")
                    riverside = Property.objects.get(name="Riverside Plaza Suite 200")
                    priya, tom = Contact.objects.get(name="Priya Natarajan"), Contact.objects.get(name="Tom Whitaker")
                    rows = [
                        ("City rental inspection", K.INSPECTION, d(9), time(10), time(12), None, maple, None, "Bring the rental license and last year's report."),
                        ("Showing: Oak Hollow Cottage", K.SHOWING, d(2), time(14), time(14, 45), None, oak, None, "Two parties booked back to back."),
                        ("Lease ends: Tom Whitaker", K.LEASE, d(18), None, None, None, maple, tom, "Renewal offer due 30 days before."),
                        ("Lease ends: Priya Natarajan", K.LEASE, d(40), None, None, None, maple, priya, ""),
                        ("Annual insurance review", K.OTHER, d(25), time(9), time(10), None, None, None, "Call the broker."),
                        ("Parking lot repaving", K.OTHER, d(5), None, None, d(7), riverside, None, "Lot closed to tenants for three days."),
                        ("Move-in inspection: Unit 4", K.INSPECTION, d(-20), time(11), time(12), None, maple, None, ""),
                    ]
                    for title, kind, start, st, et, end, prop, contact, notes in rows:
                        Event.objects.create(title=title, kind=kind, starts_on=start, start_time=st, end_time=et,
                                             ends_on=end, property=prop, contact=contact, notes=notes)
                    # Two warranty dates so the dashboard has both "ending soon" and "recently expired".
                    for name, offset in (("Samsung Dishwasher", 25), ("Bosch Dishwasher", -12)):
                        item = InventoryItem.objects.get(name=name)
                        item.warranty_expires_on = d(offset); item.save()
                    # Completed work from the past year (one older than 12 months, to show it is excluded).
                    history = [("Replace smoke detector batteries", -180, "85.00"), ("Annual boiler service", -330, "640.00"),
                               ("Test sump pumps", -33, "120.00"), ("Test sump pumps", -63, "95.50"),
                               ("Fire alarm annual inspection", -400, "999.00")]
                    for title, offset, cost in history:
                        task = MaintenanceTask.objects.get(title=title)
                        TaskOccurrence.objects.create(task=task, due_date=d(offset), status="done",
                                                      completed_on=d(offset), cost=Decimal(cost), notes="(demo history)")
                    self.stdout.write(self.style.SUCCESS(f"Sunrise Rentals: {len(rows)} events, {len(history)} history rows"))

            lakeside = Account.objects.get(name="Lakeside Holdings")
            with use_account(lakeside):
                if not Event.objects.exists():
                    Event.objects.create(title="Elevator certification", kind=K.INSPECTION, starts_on=d(6),
                                         start_time=time(9), end_time=time(11), property=Property.objects.get(name="Lakeview Tower"))
                    self.stdout.write(self.style.SUCCESS("Lakeside Holdings: 1 event"))
