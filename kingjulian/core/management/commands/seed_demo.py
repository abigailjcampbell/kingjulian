"""Create two demo tenants with realistic data. Safe to re-run (skips existing accounts)."""
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Account, Membership, User
from contacts.models import Contact, ContactProperty
from core.tenancy import use_account
from inventory.models import InventoryItem
from properties.models import Property, Unit

PASSWORD = "demo-pass-12345"
P, C, K = Property.Type, Contact.Role, InventoryItem.Category
today = date.today()

SUNRISE = {
    "properties": [
        dict(name="Maple Street Fourplex", property_type=P.MULTI_FAMILY, address_line1="412 Maple Street",
             city="Springfield", state="UT", postal_code="84101", units=["Unit 1", "Unit 2", "Unit 3", "Unit 4", "Boiler Room"]),
        dict(name="Oak Hollow Cottage", property_type=P.SINGLE_FAMILY, address_line1="88 Oak Hollow Rd",
             city="Springfield", state="UT", postal_code="84102", units=["Main House", "Garage"]),
        dict(name="Riverside Plaza Suite 200", property_type=P.COMMERCIAL, address_line1="1500 River Ave",
             city="Shelbyville", state="UT", postal_code="84103", units=["Suite 200"]),
    ],
    "contacts": [
        ("Dana Pipes", C.VENDOR, "Pipes & Sons Plumbing", "801-555-0142", "dana@pipesandsons.example", ["Maple Street Fourplex", "Oak Hollow Cottage"]),
        ("Marcus Volt", C.CONTRACTOR, "Volt Electric LLC", "801-555-0178", "marcus@voltelectric.example", ["Maple Street Fourplex", "Riverside Plaza Suite 200"]),
        ("Priya Natarajan", C.TENANT, "", "801-555-0111", "priya.n@mail.example", ["Maple Street Fourplex"]),
        ("Tom Whitaker", C.TENANT, "", "801-555-0123", "twhitaker@mail.example", ["Maple Street Fourplex"]),
        ("Lena Ortiz", C.TENANT, "Ortiz Dental", "801-555-0190", "lena@ortizdental.example", ["Riverside Plaza Suite 200"]),
        ("CoolAir HVAC Service", C.VENDOR, "CoolAir Inc.", "801-555-0166", "service@coolair.example", ["Maple Street Fourplex", "Oak Hollow Cottage", "Riverside Plaza Suite 200"]),
        ("Greg Hanover", C.OWNER, "Hanover Family Trust", "801-555-0100", "greg@hanovertrust.example", []),
    ],
    "items": [
        # (property, unit, name, category, qty, serial, purchased (days ago), cost, condition, warranty (days from today), notes)
        ("Maple Street Fourplex", "Boiler Room", "Rheem 75-gal Water Heater", K.PLUMBING, 1, "RH-7731-0042", 900, "1450.00", "good", 1800 - 900, "Serves all four units"),
        ("Maple Street Fourplex", "Boiler Room", "Lochinvar Boiler", K.HVAC, 1, "LB-2211-9087", 2400, "6800.00", "fair", -300, "Annual service due each October"),
        ("Maple Street Fourplex", "Unit 1", "Whirlpool Refrigerator", K.APPLIANCE, 1, "WP-RF-554812", 700, "899.00", "good", 365 - 700 + 365, ""),
        ("Maple Street Fourplex", "Unit 1", "GE Gas Range", K.APPLIANCE, 1, "GE-GR-332019", 1500, "749.00", "fair", -900, ""),
        ("Maple Street Fourplex", "Unit 2", "Whirlpool Refrigerator", K.APPLIANCE, 1, "WP-RF-554907", 700, "899.00", "good", 20, "Warranty about to lapse"),
        ("Maple Street Fourplex", "Unit 2", "Samsung Dishwasher", K.APPLIANCE, 1, "SS-DW-118830", 200, "625.00", "new", 530, ""),
        ("Maple Street Fourplex", "Unit 3", "Mini-split AC", K.HVAC, 1, "MS-AC-90021", 400, "2300.00", "good", 1400, "Installed by CoolAir"),
        ("Maple Street Fourplex", "Unit 3", "Smoke Detector (hardwired)", K.SAFETY, 3, "", 1100, "45.00", "good", None, "Replace batteries each spring"),
        ("Maple Street Fourplex", "Unit 4", "Fire Extinguisher 5lb ABC", K.SAFETY, 1, "FE-5-4410", 600, "62.00", "good", None, "Inspection tag expires yearly"),
        ("Maple Street Fourplex", "Unit 4", "Washer/Dryer Stack", K.APPLIANCE, 1, "LG-WD-77120", 1000, "1340.00", "poor", -200, "Drum noise reported"),
        ("Oak Hollow Cottage", "Main House", "Carrier Furnace 80k BTU", K.HVAC, 1, "CR-F80-33112", 1800, "3900.00", "good", 700, ""),
        ("Oak Hollow Cottage", "Main House", "Bosch Dishwasher", K.APPLIANCE, 1, "BS-DW-20871", 1300, "780.00", "fair", -100, ""),
        ("Oak Hollow Cottage", "Main House", "Sump Pump", K.PLUMBING, 2, "ZP-SP-1002", 500, "280.00", "good", 200, "Primary + backup"),
        ("Oak Hollow Cottage", "Garage", "Garage Door Opener", K.ELECTRICAL, 1, "LM-GD-88231", 800, "310.00", "good", 900, ""),
        ("Oak Hollow Cottage", "Garage", "Ladder 24ft Fiberglass", K.TOOL, 1, "", 2000, "240.00", "good", None, "Shared across properties"),
        ("Riverside Plaza Suite 200", "Suite 200", "Rooftop HVAC Unit 5-ton", K.HVAC, 1, "TR-RT5-77120", 1500, "8900.00", "fair", 90, "Filter change quarterly"),
        ("Riverside Plaza Suite 200", "Suite 200", "Exit Signs (LED)", K.SAFETY, 4, "", 1500, "38.00", "good", None, ""),
        ("Riverside Plaza Suite 200", "Suite 200", "Fire Alarm Panel", K.SAFETY, 1, "FA-P-30011", 2200, "2100.00", "good", 500, "Annual inspection required"),
    ],
}

LAKESIDE = {
    "properties": [
        dict(name="Lakeview Tower", property_type=P.MULTI_FAMILY, address_line1="1 Lakeshore Dr", city="Capital City",
             state="OR", postal_code="97201", units=["Penthouse", "Unit 12B"]),
    ],
    "contacts": [
        ("Beta Elevator Co", C.VENDOR, "Beta Elevator", "503-555-0190", "ops@betaelevator.example", ["Lakeview Tower"]),
    ],
    "items": [
        ("Lakeview Tower", "Penthouse", "Wine Cooler (confidential Lakeside item)", K.APPLIANCE, 1, "LW-0001", 100, "3200.00", "new", 600, ""),
        ("Lakeview Tower", "Unit 12B", "Lakeside Boiler", K.HVAC, 1, "LS-B-1", 300, "7000.00", "good", 900, ""),
    ],
}


class Command(BaseCommand):
    help = "Seed two demo tenants (Sunrise Rentals, Lakeside Holdings) with realistic data."

    def handle(self, *args, **opts):
        with transaction.atomic():
            sunrise = self._build("Sunrise Rentals", SUNRISE)
            lakeside = self._build("Lakeside Holdings", LAKESIDE)
            owner = self._user("owner@sunrise.test", sunrise, Membership.Role.OWNER)
            self._user("viewer@sunrise.test", sunrise, Membership.Role.VIEWER)
            self._user("owner@lakeside.test", lakeside, Membership.Role.OWNER)
            both = self._user("both@demo.test", sunrise, Membership.Role.MANAGER)
            Membership.objects.get_or_create(user=both, account=lakeside, defaults={"role": Membership.Role.VIEWER})
        self.stdout.write(self.style.SUCCESS(
            f"Seeded. Log in with owner@sunrise.test / viewer@sunrise.test / owner@lakeside.test / "
            f"both@demo.test  (password: {PASSWORD})"))

    def _user(self, email, account, role):
        user, _ = User.objects.get_or_create(email=email)
        user.set_password(PASSWORD); user.save()
        Membership.objects.get_or_create(user=user, account=account, defaults={"role": role})
        return user

    def _build(self, name, spec):
        account, created = Account.objects.get_or_create(name=name)
        if not created and Property.all_tenants.filter(account=account).exists():
            self.stdout.write(f"{name}: already seeded, skipping data")
            return account
        with use_account(account):
            props, units = {}, {}
            for p in spec["properties"]:
                unit_names = p.pop("units")
                prop = Property.objects.create(**p)
                props[prop.name] = prop
                for u in unit_names:
                    units[(prop.name, u)] = Unit.objects.create(property=prop, name=u)
            for cname, role, company, phone, email, linked in spec["contacts"]:
                c = Contact.objects.create(name=cname, role=role, company=company, phone=phone, email=email)
                for pname in linked:
                    ContactProperty.objects.create(contact=c, property=props[pname])
            for pname, uname, iname, cat, qty, serial, bought, cost, cond, warranty, notes in spec["items"]:
                InventoryItem.objects.create(
                    property=props[pname], unit=units[(pname, uname)], name=iname, category=cat,
                    quantity=qty, serial_number=serial, purchase_date=today - timedelta(days=bought),
                    cost=cost, condition=cond, notes=notes,
                    warranty_expires_on=(today + timedelta(days=warranty)) if warranty is not None else None)
        return account
