from django.test import TestCase

from accounts.models import Account, Membership, User
from contacts.models import Contact
from core.tenancy import use_account
from inventory.models import InventoryItem
from properties.models import Property, Unit

PW = "correct-horse-battery-1"


class World(TestCase):
    """Two tenants with realistic data, plus users covering each role."""

    @classmethod
    def setUpTestData(cls):
        cls.acme = Account.objects.create(name="Acme Rentals")
        cls.beta = Account.objects.create(name="Beta Holdings")

        def user(email, *memberships):
            u = User.objects.create_user(email, PW)
            for account, role in memberships:
                Membership.objects.create(user=u, account=account, role=role)
            return u

        R = Membership.Role
        cls.alice = user("alice@acme.test", (cls.acme, R.OWNER))
        cls.vic = user("vic@acme.test", (cls.acme, R.VIEWER))
        cls.bella = user("bella@beta.test", (cls.beta, R.OWNER))
        cls.carol = user("carol@both.test", (cls.acme, R.OWNER), (cls.beta, R.VIEWER))

        with use_account(cls.acme):
            cls.maple = Property.objects.create(name="Maple Duplex", city="Springfield",
                                                property_type=Property.Type.MULTI_FAMILY)
            cls.oak = Property.objects.create(name="Oak Cottage", city="Shelbyville")
            cls.maple_a = Unit.objects.create(property=cls.maple, name="Unit A")
            cls.maple_b = Unit.objects.create(property=cls.maple, name="Unit B")
            cls.heater = InventoryItem.objects.create(
                property=cls.maple, unit=cls.maple_a, name="Water Heater 50gal",
                category=InventoryItem.Category.HVAC, serial_number="WH-884213")
            cls.fridge = InventoryItem.objects.create(
                property=cls.oak, name="Refrigerator", category=InventoryItem.Category.APPLIANCE)
            cls.plumber = Contact.objects.create(name="Dana Pipes", role=Contact.Role.VENDOR,
                                                 company="Pipes & Co", phone="555-0101")

        with use_account(cls.beta):
            cls.lake = Property.objects.create(name="Lakeview Tower")
            cls.lake_unit = Unit.objects.create(property=cls.lake, name="Penthouse")
            cls.beta_item = InventoryItem.objects.create(property=cls.lake, name="Secret Chiller")
            cls.beta_contact = Contact.objects.create(name="Beta Vendor", role=Contact.Role.VENDOR)

    def login(self, user, account=None):
        self.client.force_login(user)
        if account is not None:
            self.client.post("/accounts/switch/", {"account_id": account.pk})
