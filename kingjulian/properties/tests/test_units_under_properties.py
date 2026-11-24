"""Units are a subsection of a property: no top-level tab or list, managed from the property's own pages."""
import csv
import io

from django.urls import reverse

from core.tenancy import use_account
from core.tests.base import World
from properties.models import Property, Unit


class NoTopLevelUnitsTests(World):
    def test_there_is_no_units_tab_and_no_units_card_on_the_dashboard(self):
        for user in (self.alice, self.vic):
            self.login(user)
            for url in ("/", "/properties/", f"/properties/{self.maple.pk}/view/"):
                body = self.client.get(url).content.decode()
                self.assertNotIn('>Units</a>', body.split("<main>")[0], url)          # the nav bar
        self.login(self.alice)
        r = self.client.get("/")
        self.assertEqual(list(r.context["d"].counts), ["Properties", "Inventory items", "Contacts"])
        self.assertNotRegex(r.content.decode(), r"\d+: Units")

    def test_the_old_units_url_sends_people_to_properties_for_everyone(self):
        for user in (self.alice, self.vic):
            self.login(user)
            r = self.client.get("/properties/units/?property=1")
            self.assertRedirects(r, "/properties/", fetch_redirect_response=False)
        self.client.logout()
        self.assertEqual(self.client.get("/properties/units/").status_code, 302)

    def test_no_unit_list_url_name_remains(self):
        from django.urls import NoReverseMatch
        with self.assertRaises(NoReverseMatch):
            reverse("unit_list")


class PropertyPageTests(World):
    def page(self, user, prop=None):
        self.login(user)
        return self.client.get(f"/properties/{(prop or self.maple).pk}/view/")

    def test_writers_see_the_units_with_an_add_button_that_prefills_the_property(self):
        r = self.page(self.alice)
        self.assertContains(r, f'href="/properties/units/new/?property={self.maple.pk}"')
        self.assertContains(r, "Unit A"); self.assertContains(r, "Unit B")
        self.assertContains(r, f'href="/properties/units/{self.maple_a.pk}/view/"')

    def test_a_property_without_units_still_offers_the_add_button_to_writers_only(self):
        r = self.page(self.alice, self.oak)
        self.assertContains(r, "No units yet."); self.assertContains(r, f'href="/properties/units/new/?property={self.oak.pk}"')
        v = self.page(self.vic, self.oak)
        self.assertNotContains(v, "No units yet."); self.assertNotContains(v, "/properties/units/new/")      # viewers: nothing to add, empty section hidden

    def test_viewers_see_units_but_no_add_button(self):
        r = self.page(self.vic)
        self.assertContains(r, "Unit A"); self.assertNotContains(r, "+ Add unit")

    def test_more_than_eight_units_link_to_the_full_list_for_this_property_only(self):
        with use_account(self.acme):
            for i in range(10):
                Unit.objects.create(property=self.maple, name=f"Extra {i:02d}")
            Unit.objects.create(property=self.oak, name="Oak-only")
        r = self.page(self.alice)
        self.assertContains(r, f'href="/properties/{self.maple.pk}/units/">See all 12')
        self.assertNotContains(r, "Oak-only")


class PropertyUnitsListTests(World):
    def get(self, user, path="", prop=None, **kw):
        self.login(user)
        return self.client.get(f"/properties/{(prop or self.maple).pk}/units/{path}", **kw)

    def test_lists_only_this_propertys_active_units(self):
        with use_account(self.acme):
            Unit.objects.create(property=self.oak, name="Oak-only")
            gone = Unit.objects.create(property=self.maple, name="Gone"); gone.archive()
        body = self.get(self.alice).content.decode()
        self.assertIn("Unit A", body); self.assertIn("Unit B", body)
        for absent in ("Oak-only", "Gone", "Penthouse"):
            self.assertNotIn(absent, body)

    def test_title_back_link_and_add_button(self):
        r = self.get(self.alice)
        self.assertContains(r, "Units · Maple Duplex")
        self.assertContains(r, f'href="/properties/{self.maple.pk}/view/">← Maple Duplex</a>')
        self.assertContains(r, f'href="/properties/units/new/?property={self.maple.pk}"')
        self.assertNotContains(self.get(self.vic), "+ New")

    def test_search_filters_and_rows_open_the_unit_page(self):
        r = self.get(self.alice, "?q=unit+b", HTTP_HX_REQUEST="true")
        self.assertContains(r, "Unit B"); self.assertNotContains(r, "Unit A")
        self.assertContains(r, f'href="/properties/units/{self.maple_b.pk}/view/"')
        self.assertNotContains(r, f'href="/properties/units/{self.maple_b.pk}/"')                          # never straight to edit

    def test_other_accounts_and_archived_properties_404(self):
        self.login(self.alice)
        self.assertEqual(self.client.get(f"/properties/{self.lake.pk}/units/").status_code, 404)
        self.assertEqual(self.client.get("/properties/999999/units/").status_code, 404)
        with use_account(self.acme):
            self.oak.archive()
        self.assertEqual(self.client.get(f"/properties/{self.oak.pk}/units/").status_code, 404)
        # the CSV export never renders the page title, so it must be refused on its own
        self.assertEqual(self.client.get(f"/properties/{self.lake.pk}/units/?export=csv").status_code, 404)
        self.assertEqual(self.client.get(f"/properties/{self.oak.pk}/units/?export=csv").status_code, 404)

    def test_pagination_and_export_follow_the_property(self):
        with use_account(self.acme):
            for i in range(30):
                Unit.objects.create(property=self.maple, name=f"Bulk {i:02d}")
            Unit.objects.create(property=self.oak, name="Oak-only")
        r = self.get(self.alice)
        self.assertEqual(len(r.context["object_list"]), 25); self.assertContains(r, "(32 total)")
        self.assertContains(r, "?page=2")
        csv_r = self.get(self.alice, "?export=csv")
        self.assertEqual(csv_r.status_code, 200)
        rows = list(csv.reader(io.StringIO(csv_r.content.decode("utf-8-sig"))))
        names = [row[0] for row in rows[1:]] + [c for row in rows[1:] for c in row]
        self.assertEqual(len(rows) - 1, 32); self.assertNotIn("Oak-only", names)
        self.assertEqual(self.get(self.vic, "?export=csv").status_code, 403)


class UnitFlowTests(World):
    def test_add_unit_from_a_property_prefills_it_and_returns_to_that_propertys_units(self):
        self.login(self.alice)
        r = self.client.get(f"/properties/units/new/?property={self.oak.pk}")
        self.assertEqual(r.context["form"].initial["property"], self.oak.pk)
        self.assertContains(r, f'href="/properties/{self.oak.pk}/view/">Cancel')
        r = self.client.post(f"/properties/units/new/?property={self.oak.pk}", {"property": self.oak.pk, "name": "Studio", "notes": ""}, follow=True)
        self.assertEqual(r.redirect_chain[-1][0], f"/properties/{self.oak.pk}/units/")
        self.assertContains(r, "Saved Oak Cottage / Studio."); self.assertContains(r, 'href="/properties/units/')
        with use_account(self.acme):
            self.assertEqual(Unit.objects.get(name="Studio").property_id, self.oak.pk)

    def test_prefill_ignores_junk_and_other_accounts_properties(self):
        self.login(self.alice)
        for bad in ("abc", "", "-1", str(self.lake.pk), "999999"):
            r = self.client.get(f"/properties/units/new/?property={bad}")
            self.assertEqual(r.status_code, 200, bad)
            self.assertNotIn("property", r.context["form"].initial, bad)
            self.assertContains(r, 'href="/properties/">Cancel', msg_prefix=bad)
            self.assertNotContains(r, f'value="{self.lake.pk}"', msg_prefix=bad) if bad == str(self.lake.pk) else None

    def test_cannot_create_a_unit_under_another_accounts_property(self):
        self.login(self.alice)
        r = self.client.post("/properties/units/new/", {"property": self.lake.pk, "name": "Sneaky"})
        self.assertEqual(r.status_code, 200)
        with use_account(self.acme):
            self.assertFalse(Unit.objects.filter(name="Sneaky").exists())

    def test_editing_a_unit_cancels_and_saves_back_to_its_page_and_unit_page_goes_back_to_the_property(self):
        self.login(self.alice)
        r = self.client.get(f"/properties/units/{self.maple_a.pk}/")
        self.assertContains(r, f'href="/properties/units/{self.maple_a.pk}/view/">Cancel')
        r = self.client.post(f"/properties/units/{self.maple_a.pk}/", {"property": self.maple.pk, "name": "Unit A2", "notes": ""}, follow=True)
        self.assertEqual(r.redirect_chain[-1][0], f"/properties/units/{self.maple_a.pk}/view/")
        self.assertContains(r, f'href="/properties/{self.maple.pk}/view/">Back to Maple Duplex</a>')
        self.assertNotContains(r, "Back to units")

    def test_archiving_a_unit_returns_to_its_property_with_an_undo_link(self):
        self.login(self.alice)
        r = self.client.post(f"/properties/units/{self.maple_b.pk}/archive/", follow=True)
        self.assertEqual(r.redirect_chain[-1][0], f"/properties/{self.maple.pk}/view/")
        self.assertContains(r, f'<a href="/archive/unit/{self.maple_b.pk}/">Undo</a>')
        self.assertNotContains(r, "Unit B</a>")
        self.client.post(f"/archive/unit/{self.maple_b.pk}/restore/")
        self.assertContains(self.client.get(f"/properties/{self.maple.pk}/view/"), "Unit B")

    def test_viewers_cannot_add_edit_or_archive_units(self):
        self.login(self.vic)
        self.assertEqual(self.client.get("/properties/units/new/").status_code, 403)
        self.assertEqual(self.client.get(f"/properties/units/{self.maple_a.pk}/").status_code, 403)
        self.assertEqual(self.client.post(f"/properties/units/{self.maple_a.pk}/archive/").status_code, 403)
        self.assertEqual(self.client.get(f"/properties/units/{self.maple_a.pk}/view/").status_code, 200)

    def test_unit_pages_of_other_accounts_404(self):
        self.login(self.alice)
        for path in ("view/", "", "archive/"):
            method = self.client.post if path == "archive/" else self.client.get
            self.assertEqual(method(f"/properties/units/{self.lake_unit.pk}/{path}").status_code, 404, path)
