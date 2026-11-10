from django.test import override_settings

from accounts.models import Account, Membership, User
from contacts.forms import ContactForm
from contacts.models import Contact, ContactProperty
from core.tenancy import use_account
from inventory.forms import InventoryItemForm
from inventory.models import InventoryItem
from properties.forms import PropertyForm, UnitForm
from properties.models import Property

from .base import PW, World


class AuthAndRoleTests(World):
    def test_anonymous_redirected_to_login(self):
        for url in ("/", "/properties/", "/inventory/", "/contacts/", "/properties/units/"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn("/accounts/login/", r["Location"])

    def test_all_list_pages_render_for_owner(self):
        self.login(self.alice)
        for url in ("/", "/properties/", "/inventory/", "/contacts/", "/properties/units/",
                    "/properties/new/", "/inventory/new/", "/contacts/new/", "/properties/units/new/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_viewer_can_read_but_not_write(self):
        self.login(self.vic)
        self.assertEqual(self.client.get("/properties/").status_code, 200)
        self.assertNotContains(self.client.get("/properties/"), "+ New")
        self.assertEqual(self.client.get("/properties/new/").status_code, 403)
        r = self.client.post("/properties/new/", {"name": "Nope", "property_type": "other"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.post(f"/properties/{self.maple.pk}/archive/").status_code, 403)
        with use_account(self.acme):
            self.assertFalse(Property.objects.filter(name="Nope").exists())
            self.assertTrue(Property.objects.active().filter(pk=self.maple.pk).exists())


class IsolationTests(World):
    def test_list_shows_only_own_account(self):
        self.login(self.alice)
        for url in ("/properties/", "/inventory/", "/contacts/", "/properties/units/"):
            body = self.client.get(url).content.decode()
            for leaked in ("Lakeview Tower", "Secret Chiller", "Beta Vendor", "Penthouse"):
                self.assertNotIn(leaked, body, f"{leaked} leaked on {url}")

    def test_other_accounts_objects_404_on_edit_and_archive(self):
        self.login(self.alice)
        for url in (f"/properties/{self.lake.pk}/", f"/inventory/{self.beta_item.pk}/",
                    f"/contacts/{self.beta_contact.pk}/", f"/properties/units/{self.lake_unit.pk}/"):
            self.assertEqual(self.client.get(url).status_code, 404, url)
            self.assertEqual(self.client.post(url, {"name": "hijack"}).status_code, 404, url)
            self.assertEqual(self.client.post(url + "archive/").status_code, 404, url)
        with use_account(self.beta):
            self.assertTrue(Property.objects.active().filter(pk=self.lake.pk).exists())
            self.assertEqual(Property.objects.get(pk=self.lake.pk).name, "Lakeview Tower")

    def test_forged_foreign_property_id_on_create_is_rejected(self):
        self.login(self.alice)
        r = self.client.post("/inventory/new/", {
            "property": self.lake.pk, "name": "Injected", "category": "other",
            "quantity": 1, "condition": "good"})
        self.assertEqual(r.status_code, 200)                    # re-rendered with errors
        self.assertTrue(r.context["form"].errors.get("property"))
        self.assertEqual(InventoryItem.all_tenants.filter(name="Injected").count(), 0)

    def test_forged_foreign_unit_id_is_rejected(self):
        self.login(self.alice)
        r = self.client.post("/inventory/new/", {
            "property": self.maple.pk, "unit": self.lake_unit.pk, "name": "Injected2",
            "category": "other", "quantity": 1, "condition": "good"})
        self.assertTrue(r.context["form"].errors.get("unit"))
        self.assertEqual(InventoryItem.all_tenants.filter(name="Injected2").count(), 0)

    def test_unit_from_wrong_property_rejected_by_form(self):
        self.login(self.alice)
        r = self.client.post("/inventory/new/", {
            "property": self.oak.pk, "unit": self.maple_a.pk, "name": "Mismatch",
            "category": "other", "quantity": 1, "condition": "good"})
        self.assertTrue(r.context["form"].errors.get("unit"))

    def test_account_switch_changes_visible_data(self):
        self.login(self.carol)
        self.assertContains(self.client.get("/properties/"), "Maple Duplex")
        self.client.post("/accounts/switch/", {"account_id": self.beta.pk})
        body = self.client.get("/properties/").content.decode()
        self.assertIn("Lakeview Tower", body)
        self.assertNotIn("Maple Duplex", body)
        # carol is only a viewer in Beta
        self.assertEqual(self.client.get("/properties/new/").status_code, 403)

    def test_cannot_switch_into_account_without_membership(self):
        self.login(self.alice)
        r = self.client.post("/accounts/switch/", {"account_id": self.beta.pk})
        self.assertEqual(r.status_code, 403)
        self.assertNotContains(self.client.get("/properties/"), "Lakeview Tower")

    def test_tampered_session_account_is_ignored(self):
        self.login(self.alice)
        s = self.client.session
        s["account_id"] = self.beta.pk          # alice is not a member of Beta
        s.save()
        body = self.client.get("/properties/").content.decode()
        self.assertIn("Maple Duplex", body)
        self.assertNotIn("Lakeview Tower", body)


class CrudFlowTests(World):
    def setUp(self):
        self.login(self.alice)

    def test_create_edit_archive_property(self):
        r = self.client.post("/properties/new/", {
            "name": "Birch Lodge", "property_type": "single_family", "city": "Ogdenville"})
        self.assertRedirects(r, "/properties/")
        with use_account(self.acme):
            p = Property.objects.get(name="Birch Lodge")
        self.assertEqual(p.account_id, self.acme.pk)
        self.client.post(f"/properties/{p.pk}/", {
            "name": "Birch Lodge II", "property_type": "single_family", "city": "Ogdenville"})
        p.refresh_from_db()
        self.assertEqual(p.name, "Birch Lodge II")
        self.client.post(f"/properties/{p.pk}/archive/")
        # Check the results fragment only: the full page also renders the flash message.
        table = self.client.get("/properties/", HTTP_HX_REQUEST="true")
        self.assertNotContains(table, "Birch Lodge II")
        self.assertEqual(Property.all_tenants.get(pk=p.pk).is_archived, True)

    def test_create_inventory_item_with_unit(self):
        r = self.client.post("/inventory/new/", {
            "property": self.maple.pk, "unit": self.maple_b.pk, "name": "Dishwasher",
            "category": "appliance", "quantity": 1, "cost": "649.99", "condition": "new",
            "purchase_date": "2025-03-14", "warranty_expires_on": "2027-03-14"})
        self.assertRedirects(r, "/inventory/")
        item = InventoryItem.all_tenants.get(name="Dishwasher")
        self.assertEqual((item.account_id, item.unit_id), (self.acme.pk, self.maple_b.pk))

    def test_contact_property_links_sync(self):
        base = {"name": "Dana Pipes", "role": "vendor"}
        self.client.post(f"/contacts/{self.plumber.pk}/", {**base, "properties": [self.maple.pk, self.oak.pk]})
        links = ContactProperty.all_tenants.filter(contact=self.plumber)
        self.assertEqual({l.property_id for l in links}, {self.maple.pk, self.oak.pk})
        self.assertTrue(all(l.account_id == self.acme.pk for l in links))
        self.client.post(f"/contacts/{self.plumber.pk}/", {**base, "properties": [self.oak.pk]})
        self.assertEqual({l.property_id for l in ContactProperty.all_tenants.filter(contact=self.plumber)},
                         {self.oak.pk})
        self.client.post(f"/contacts/{self.plumber.pk}/", base)
        self.assertEqual(ContactProperty.all_tenants.filter(contact=self.plumber).count(), 0)

    def test_contact_cannot_link_foreign_property(self):
        r = self.client.post(f"/contacts/{self.plumber.pk}/", {
            "name": "Dana Pipes", "role": "vendor", "properties": [self.lake.pk]})
        self.assertTrue(r.context["form"].errors.get("properties"))
        self.assertEqual(ContactProperty.all_tenants.filter(property=self.lake).count(), 0)

    def test_edit_form_prepopulates_contact_properties(self):
        with use_account(self.acme):
            ContactProperty.objects.create(contact=self.plumber, property=self.maple)
        r = self.client.get(f"/contacts/{self.plumber.pk}/")
        self.assertEqual(r.context["form"].fields["properties"].initial, [self.maple.pk])


class SearchAndFilterTests(World):
    def setUp(self):
        self.login(self.alice)

    def test_text_search_matches_name_and_serial(self):
        body = self.client.get("/inventory/?q=heater").content.decode()
        self.assertIn("Water Heater", body); self.assertNotIn("Refrigerator", body)
        body = self.client.get("/inventory/?q=884213").content.decode()
        self.assertIn("Water Heater", body); self.assertNotIn("Refrigerator", body)

    def test_search_across_relation(self):
        body = self.client.get("/inventory/?q=oak").content.decode()    # property name
        self.assertIn("Refrigerator", body); self.assertNotIn("Water Heater", body)

    def test_choice_and_fk_filters(self):
        body = self.client.get("/inventory/?category=hvac").content.decode()
        self.assertIn("Water Heater", body); self.assertNotIn("Refrigerator", body)
        body = self.client.get(f"/inventory/?property={self.oak.pk}").content.decode()
        self.assertIn("Refrigerator", body); self.assertNotIn("Water Heater", body)
        body = self.client.get("/contacts/?role=vendor").content.decode()
        self.assertIn("Dana Pipes", body)
        self.assertNotIn("Dana Pipes", self.client.get("/contacts/?role=tenant").content.decode())

    def test_foreign_or_garbage_filter_values_are_ignored_not_errors(self):
        for qs in (f"property={self.lake.pk}", "property=abc", "category=nonsense", "property="):
            r = self.client.get(f"/inventory/?{qs}")
            self.assertEqual(r.status_code, 200, qs)
            self.assertNotIn("Secret Chiller", r.content.decode(), qs)

    def test_htmx_request_returns_fragment_only(self):
        full = self.client.get("/inventory/").content.decode()
        part = self.client.get("/inventory/?q=heater", HTTP_HX_REQUEST="true").content.decode()
        self.assertIn("<html", full); self.assertNotIn("<html", part)
        self.assertIn("Water Heater", part)

    def test_pagination_preserves_filters(self):
        with use_account(self.acme):
            for i in range(30):
                InventoryItem.objects.create(property=self.oak, name=f"Smoke Detector {i:02d}",
                                             category=InventoryItem.Category.SAFETY)
        r = self.client.get("/inventory/?category=safety")
        self.assertEqual(len(r.context["object_list"]), 25)
        self.assertContains(r, "category=safety&page=2")
        self.assertEqual(len(self.client.get("/inventory/?category=safety&page=2").context["object_list"]), 5)


class SignupAndFormTests(World):
    def test_signup_creates_isolated_owner_account(self):
        r = self.client.post("/accounts/signup/", {
            "account_name": "Gamma LLC", "email": "Gus@Gamma.test",
            "password1": PW, "password2": PW})
        self.assertRedirects(r, "/")
        u = User.objects.get(email="gus@gamma.test")
        m = Membership.objects.get(user=u)
        self.assertEqual((m.account.name, m.role), ("Gamma LLC", Membership.Role.OWNER))
        body = self.client.get("/properties/").content.decode()
        self.assertNotIn("Maple Duplex", body); self.assertNotIn("Lakeview Tower", body)

    def test_signup_rejects_duplicate_email_case_insensitively(self):
        r = self.client.post("/accounts/signup/", {
            "account_name": "Dup", "email": "ALICE@acme.test", "password1": PW, "password2": PW})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context["form"].errors.get("email"))

    def test_no_model_form_exposes_account(self):
        for form in (PropertyForm, UnitForm, ContactForm, InventoryItemForm):
            self.assertNotIn("account", form.base_fields, form.__name__)
            self.assertNotIn("archived_at", form.base_fields, form.__name__)


class RowLinkTests(World):
    def test_viewers_are_not_offered_links_to_pages_that_403(self):
        edit_links = [f'href="/properties/{self.maple.pk}/"', f'href="/contacts/{self.plumber.pk}/"',
                      f'href="/inventory/{self.heater.pk}/"']
        self.login(self.vic)
        for url, link in zip(("/properties/", "/contacts/", "/inventory/"), edit_links):
            self.assertNotContains(self.client.get(url), link)
            self.assertEqual(self.client.get(link.split('"')[1]).status_code, 403)   # and the page really is forbidden
        self.client.logout(); self.login(self.alice)
        for url, link in zip(("/properties/", "/contacts/", "/inventory/"), edit_links):
            self.assertContains(self.client.get(url), link)

    def test_filter_dropdowns_read_naturally(self):
        self.login(self.alice)
        body = self.client.get("/inventory/").content.decode()
        self.assertIn("Any property", body); self.assertNotIn("propertys", body)
