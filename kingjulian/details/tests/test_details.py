import re
from datetime import date as D, timedelta
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import Membership, User
from archive.registry import KINDS
from archive.tests.base import ArchiveWorld
from audit.context import acting_as
from contacts.models import Contact, ContactProperty
from core.tenancy import use_account
from core.tests.base import PW
from details.registry import DETAIL_NAMES, EDIT_NAMES, record_url
from inventory.models import InventoryItem
from maintenance.models import MaintenanceTask, TaskOccurrence
from maintenance.services import complete_occurrence, ensure_first_occurrence
from planner.models import Event
from properties.models import Property, Unit

from attachments.tests.base import png

XSS = "<script>alert(1)</script>"
VIEW = {"property": "/properties/{}/view/", "unit": "/properties/units/{}/view/", "item": "/inventory/{}/view/",
        "contact": "/contacts/{}/view/", "task": "/maintenance/tasks/{}/view/", "event": "/planner/events/{}/view/"}
EDIT = {"property": "/properties/{}/", "unit": "/properties/units/{}/", "item": "/inventory/{}/",
        "contact": "/contacts/{}/", "task": "/maintenance/tasks/{}/", "event": "/planner/events/{}/"}


def table(response):
    """{label: visible text} from the read-only field table."""
    out = {}
    for label, value in re.findall(r"<th[^>]*>(.*?)</th><td>(.*?)</td>", response.content.decode(), flags=re.S):
        out[re.sub(r"<[^>]+>", "", label).strip()] = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()
    return out


def sections(response):
    """{section title: [row texts]}"""
    html = response.content.decode()
    out = {}
    for block in re.findall(r'<section class="rel">(.*?)</section>', html, flags=re.S):
        title = re.sub(r"<[^>]+>", "", re.search(r"<h2>(.*?)<small>", block, flags=re.S).group(1)).strip()
        out[title] = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", li)).strip() for li in re.findall(r"<li>(.*?)</li>", block, flags=re.S)]
    return out


class DetailBase(ArchiveWorld):
    def setUp(self):
        self.login(self.alice)
        self.objs = self.make_all()
        self.mgr = User.objects.create_user("mgr@acme.test", PW)
        Membership.objects.create(user=self.mgr, account=self.acme, role="manager")

    def get(self, key, user=None):
        if user:
            self.login(user)
        return self.client.get(VIEW[key].format(self.objs[key].pk))


class RegistryTests(DetailBase):
    def test_every_archivable_type_has_a_detail_edit_and_url(self):
        self.assertEqual(set(DETAIL_NAMES), set(KINDS)); self.assertEqual(set(EDIT_NAMES), set(KINDS))
        for key, obj in self.objs.items():
            self.assertEqual(record_url(obj), VIEW[key].format(obj.pk))

    def test_no_link_is_offered_for_archived_or_unsupported_records(self):
        self.archive(self.objs["contact"])
        self.assertIsNone(record_url(self.fresh(self.objs["contact"])))
        self.assertIsNone(record_url(self.alice))


class AccessTests(DetailBase):
    def test_every_role_can_open_every_type(self):
        for user in (self.alice, self.mgr, self.vic):
            for key in self.objs:
                self.assertEqual(self.get(key, user).status_code, 200, (user.email, key))

    def test_anonymous_other_accounts_and_missing_records(self):
        self.client.logout()
        for key, obj in self.objs.items():
            r = self.client.get(VIEW[key].format(obj.pk))
            self.assertEqual(r.status_code, 302); self.assertIn("/accounts/login/", r["Location"])
        self.login(self.bella)
        for key, obj in self.objs.items():
            self.assertEqual(self.client.get(VIEW[key].format(obj.pk)).status_code, 404, key)
        self.login(self.alice)
        for key in self.objs:
            self.assertEqual(self.client.get(VIEW[key].format(999999)).status_code, 404, key)

    def test_archived_records_go_to_the_archive_page_for_writers_and_are_hidden_from_viewers(self):
        self.archive(self.objs["contact"])
        r = self.client.get(VIEW["contact"].format(self.objs["contact"].pk))
        self.assertRedirects(r, f"/archive/contact/{self.objs['contact'].pk}/", fetch_redirect_response=False)
        self.login(self.vic); self.assertEqual(self.client.get(VIEW["contact"].format(self.objs["contact"].pk)).status_code, 404)
        self.login(self.alice)
        self.client.post(f"/archive/contact/{self.objs['contact'].pk}/restore/")
        self.assertEqual(self.client.get(VIEW["contact"].format(self.objs["contact"].pk)).status_code, 200)


class ReadOnlyBehaviourTests(DetailBase):
    def test_the_page_is_information_not_a_form(self):
        for key in self.objs:
            r = self.get(key)
            self.assertNotContains(r, 'class="crud"'); self.assertNotContains(r, "<textarea"); self.assertNotContains(r, 'name="name"')
            self.assertNotContains(r, 'type="submit">Save')

    def test_edit_archive_and_back_buttons_by_role(self):
        for key, obj in self.objs.items():
            r = self.get(key, self.alice)
            self.assertContains(r, f'<a class="btn" href="{EDIT[key].format(obj.pk)}">Edit</a>', msg_prefix=key)
            self.assertContains(r, f'action="/{"properties/units" if key == "unit" else {"property":"properties","item":"inventory","contact":"contacts","task":"maintenance/tasks","event":"planner/events"}.get(key, key)}/{obj.pk}/archive/"')
            self.assertContains(r, "Back to ")
            self.get(key, self.mgr); self.assertContains(self.client.get(VIEW[key].format(obj.pk)), ">Edit</a>")
            v = self.get(key, self.vic)
            self.assertNotContains(v, ">Edit</a>", msg_prefix=key); self.assertNotContains(v, "/archive/"); self.assertContains(v, "Back to ")

    def test_the_view_itself_withholds_edit_and_archive_urls_from_viewers(self):
        """The template also hides the buttons; this pins the view's own layer so neither depends on the other."""
        for key, obj in self.objs.items():
            w, v = self.get(key, self.alice), self.get(key, self.vic)
            self.assertEqual((w.context["edit_url"], w.context["can_write"]), (EDIT[key].format(obj.pk), True), key)
            self.assertTrue(w.context["archive_url"].endswith("/archive/"), key)
            self.assertEqual((v.context["edit_url"], v.context["archive_url"], v.context["can_write"]), (None, None, False), key)

    def test_the_edit_button_leads_to_the_form_and_cancel_returns_to_the_record(self):
        for key, obj in self.objs.items():
            edit = self.client.get(EDIT[key].format(obj.pk))
            self.assertEqual(edit.status_code, 200, key); self.assertContains(edit, 'class="crud"')
            self.assertContains(edit, f'<a class="btn secondary" href="{VIEW[key].format(obj.pk)}">Cancel</a>', msg_prefix=key)

    def test_saving_an_edit_lands_back_on_the_record_with_a_confirmation(self):
        pk = self.objs["contact"].pk
        r = self.client.post(f"/contacts/{pk}/", {"name": "Zed Roofer Jr", "role": "contractor", "company": "Roofers Inc"}, follow=True)
        self.assertEqual(r.redirect_chain[-1][0], f"/contacts/{pk}/view/")
        self.assertContains(r, "Saved Zed Roofer Jr."); self.assertEqual(table(r)["Name"], "Zed Roofer Jr")

    def test_a_failed_edit_stays_on_the_form_and_cancel_still_points_back(self):
        pk = self.objs["contact"].pk
        r = self.client.post(f"/contacts/{pk}/", {"name": "", "role": "contractor"})
        self.assertEqual(r.status_code, 200); self.assertContains(r, 'class="crud"')
        self.assertContains(r, f'href="/contacts/{pk}/view/">Cancel')

    def test_creating_a_record_still_returns_to_the_list(self):
        r = self.client.post("/contacts/new/", {"name": "Brand New", "role": "vendor"})
        self.assertRedirects(r, "/contacts/", fetch_redirect_response=False)
        self.assertContains(self.client.get("/contacts/new/"), 'href="/contacts/">Cancel')

    def test_archiving_from_the_page_works_and_offers_undo(self):
        pk = self.objs["contact"].pk
        r = self.client.post(f"/contacts/{pk}/archive/", follow=True)
        self.assertContains(r, f'<a href="/archive/contact/{pk}/">Undo</a>')

    def test_user_text_is_escaped_and_line_breaks_are_kept_safely(self):
        with use_account(self.acme):
            p = Property.objects.create(name=XSS, notes="line one\n" + XSS)
        r = self.client.get(VIEW["property"].format(p.pk))
        self.assertNotContains(r, XSS); self.assertContains(r, "&lt;script&gt;"); self.assertContains(r, "line one<br>")


class FieldAndLinkTests(DetailBase):
    def test_item_fields_links_and_warranty_status(self):
        today = timezone.localdate()
        with use_account(self.acme):
            it = self.objs["item"]
            it.warranty_expires_on = today + timedelta(days=25); it.save()
        r = self.get("item"); t = table(r)
        self.assertEqual((t["Name"], t["Category"], t["Condition"], t["Property"], t["Unit"], t["Warranty status"]),
                         ("Sump Pump", "Plumbing", "Good", "Birch Lodge", "Birch Lodge / Cellar", "Expires in 25 days"))
        self.assertContains(r, f'<a href="/properties/{self.objs["property"].pk}/view/">Birch Lodge</a>')
        self.assertContains(r, f'<a href="/properties/units/{self.objs["unit"].pk}/view/">Birch Lodge / Cellar</a>')
        for delta, want in ((0, "Expires today"), (1, "Expires in 1 day"), (-1, "Expired 1 day ago"), (-12, "Expired 12 days ago")):
            with use_account(self.acme):
                it.warranty_expires_on = today + timedelta(days=delta); it.save()
            self.assertEqual(table(self.get("item"))["Warranty status"], want)
        with use_account(self.acme):
            it.warranty_expires_on = None; it.save()
        self.assertNotIn("Warranty status", table(self.get("item")))

    def test_links_only_go_to_live_records(self):
        self.archive(self.objs["unit"])
        t = table(self.get("item"))
        self.assertEqual(t["Unit"], "Birch Lodge / Cellar")
        self.assertNotContains(self.get("item"), f'href="/properties/units/{self.objs["unit"].pk}/view/"')        # archived: text, not a link

    def test_event_and_contact_links(self):
        with use_account(self.acme):
            ev = self.objs["event"]; ev.contact = self.objs["contact"]; ev.save()
        r = self.get("event")
        self.assertContains(r, f'<a href="/contacts/{self.objs["contact"].pk}/view/">Zed Roofer</a>')
        self.assertContains(r, f'<a href="/properties/{self.objs["property"].pk}/view/">Birch Lodge</a>')
        self.assertEqual(table(r)["Starts on"], "Dec 5, 2026")

    def test_blank_values_are_dashes_not_none(self):
        t = table(self.get("contact"))
        self.assertEqual((t["Email"], t["Phone"], t["Notes"]), ("—", "—", "—"))
        for v in table(self.get("property")).values():
            self.assertNotIn("None", v)

    def test_internal_columns_never_show(self):
        for key in self.objs:
            for hidden in ("Id", "Account", "Created at", "Updated at", "Archived at"):
                self.assertNotIn(hidden, table(self.get(key)), key)


class RelatedSectionTests(DetailBase):
    def test_property_shows_everything_that_belongs_to_it(self):
        with use_account(self.acme):
            ContactProperty.objects.create(contact=self.objs["contact"], property=self.objs["property"])
        s = sections(self.get("property"))
        self.assertEqual(list(s), ["Units", "Inventory", "Maintenance tasks", "Contacts", "Events"])
        self.assertEqual(s["Units"], ["Cellar"])
        self.assertEqual(s["Inventory"], ["Sump Pump Cellar · Plumbing · Good"])        # the property is implied, so only the unit
        self.assertTrue(s["Maintenance tasks"][0].startswith("Check sump Yearly"), s["Maintenance tasks"])
        self.assertEqual(s["Contacts"], ["Zed Roofer Roofers Inc"]); self.assertTrue(s["Events"][0].startswith("Walkthrough"))
        r = self.get("property")
        self.assertContains(r, f'href="/inventory/{self.objs["item"].pk}/view/"'); self.assertContains(r, f'href="/contacts/{self.objs["contact"].pk}/view/"')

    def test_only_active_records_of_this_account_are_listed(self):
        self.archive(self.objs["unit"]); self.archive(self.objs["event"])
        with use_account(self.beta):
            Unit.objects.create(property=self.lake, name="Penthouse2")
        s = sections(self.get("property"))
        self.assertNotIn("Units", s); self.assertNotIn("Events", s)                                    # archived ones are gone; empty sections hidden
        self.assertNotIn("Penthouse2", str(s))

    def test_long_lists_are_cut_with_an_exact_total_and_a_filtered_see_all_link(self):
        with use_account(self.acme):
            for i in range(11):
                Unit.objects.create(property=self.objs["property"], name=f"U{i:02d}")
                InventoryItem.objects.create(property=self.objs["property"], name=f"Item{i:02d}")
        r = self.get("property"); s = sections(r)
        self.assertEqual((len(s["Units"]), len(s["Inventory"])), (8, 8))
        self.assertContains(r, "<small>12</small>")                                                    # 1 + 11 units
        self.assertContains(r, f'href="/properties/units/?property={self.objs["property"].pk}">See all 12')
        self.assertContains(r, f'href="/inventory/?property={self.objs["property"].pk}">See all 12')
        self.assertEqual(len(self.client.get(f"/properties/units/?property={self.objs['property'].pk}").context["object_list"]), 12)

    def test_unit_item_and_contact_sections(self):
        with use_account(self.acme):
            t = self.objs["task"]; t.unit = self.objs["unit"]; t.item = self.objs["item"]; t.assigned_contact = self.objs["contact"]; t.save()
            ev = self.objs["event"]; ev.contact = self.objs["contact"]; ev.save()
            ContactProperty.objects.create(contact=self.objs["contact"], property=self.objs["property"])
        self.assertEqual(list(sections(self.get("unit"))), ["Inventory in this unit", "Maintenance tasks"])
        self.assertEqual(list(sections(self.get("item"))), ["Maintenance tasks"])
        c = sections(self.get("contact"))
        self.assertEqual(list(c), ["Properties", "Assigned maintenance tasks", "Events"])
        self.assertEqual(c["Properties"], ["Birch Lodge Ogdenville"])

    def test_task_page_shows_schedule_history_costs_and_job_links(self):
        today = timezone.localdate()
        with use_account(self.acme):
            t = MaintenanceTask.objects.create(property=self.objs["property"], title="Flush heater", start_date=today - timedelta(days=70),
                                               recurrence_rule="FREQ=MONTHLY", assigned_contact=self.objs["contact"], item=self.objs["item"])
            ensure_first_occurrence(t)
            for cost in ("85.50", "640.00"):
                complete_occurrence(TaskOccurrence.objects.get(task=t, status="open"), completed_on=today - timedelta(days=20), cost=Decimal(cost))
        r = self.client.get(VIEW["task"].format(t.pk)); f = table(r)
        self.assertEqual((f["Repeats"], f["Completed jobs"], f["Total recorded cost"], f["Assigned contact"], f["Item"]),
                         ("Monthly", "2", "$725.50", "Zed Roofer", "Sump Pump"))
        self.assertRegex(f["Next due"], r"^[A-Z][a-z]{2} \d{1,2}, \d{4} \(.+\)$")
        jobs = sections(r)["Jobs"]
        self.assertEqual(len(jobs), 3)
        self.assertTrue(any("Done" in j and "$640.00" in j for j in jobs)); self.assertTrue(any(j.split(" ")[3] == "Open" or "Open" in j for j in jobs))
        with use_account(self.acme):
            occ = TaskOccurrence.objects.filter(task=t).first()
        self.assertContains(r, f'href="/maintenance/{occ.pk}/"')
        self.assertContains(r, f'<a href="/contacts/{self.objs["contact"].pk}/view/">Zed Roofer</a>')
        self.assertNotIn("Recurrence rule", f); self.assertNotIn("Schedule mode", f)

    def test_a_task_with_no_history_says_so(self):
        with use_account(self.acme):
            t = MaintenanceTask.objects.create(property=self.objs["property"], title="Never started", start_date=D(2030, 1, 1), recurrence_rule="")
        f = table(self.client.get(VIEW["task"].format(t.pk)))
        self.assertEqual((f["Next due"], f["Completed jobs"]), ("Nothing scheduled", "0")); self.assertNotIn("Total recorded cost", f)
        self.assertNotIn("Jobs", sections(self.client.get(VIEW["task"].format(t.pk))))


class PanelTests(DetailBase):
    def test_files_and_history_panels_by_role(self):
        att = self.store(self.objs["property"], "plans.png", png())
        with acting_as(self.alice), use_account(self.acme):
            p = Property.objects.get(pk=self.objs["property"].pk); p.notes = "changed"; p.save()
        r = self.get("property", self.alice)
        self.assertContains(r, "<h2>Files</h2>"); self.assertContains(r, "plans.png"); self.assertContains(r, 'type="file"')
        self.assertContains(r, "<h2>History"); self.assertContains(r, "Updated property")
        v = self.get("property", self.vic)
        self.assertContains(v, "plans.png"); self.assertContains(v, f'href="/files/{att.pk}/download/"')
        self.assertNotContains(v, 'type="file"'); self.assertNotContains(v, "<h2>History")


class RowLinkTests(DetailBase):
    def test_every_list_opens_the_read_only_page_and_other_lists_keep_their_own_pages(self):
        lists = {"property": "/properties/", "unit": "/properties/units/", "item": "/inventory/", "contact": "/contacts/",
                 "task": "/maintenance/tasks/", "event": "/planner/events/"}
        for user in (self.alice, self.vic):
            self.login(user)
            for key, url in lists.items():
                pk = self.objs[key].pk
                r = self.client.get(url)
                self.assertContains(r, f'href="{VIEW[key].format(pk)}"', msg_prefix=f"{user.email} {key}")
                self.assertNotContains(r, f'href="{EDIT[key].format(pk)}"')
        occ = self.occ(self.acme, self.maple, "Pump", timezone.localdate())
        att = self.store(self.maple, "a.png", png())
        for user in (self.alice, self.vic):
            self.login(user)
            self.assertContains(self.client.get("/maintenance/"), f'href="/maintenance/{occ.pk}/"')      # jobs keep their action page
            self.assertContains(self.client.get("/files/"), f'href="/files/{att.pk}/"')

    def test_pages_that_link_to_records_use_the_read_only_page(self):
        today = timezone.localdate()
        with use_account(self.acme):
            e = self.objs["event"]; e.starts_on = today + timedelta(days=2); e.save()
            i = self.objs["item"]; i.warranty_expires_on = today + timedelta(days=5); i.condition = "replace"; i.save()
        dash = self.client.get("/").content.decode()
        self.assertIn(f'href="/planner/events/{e.pk}/view/"', dash); self.assertIn(f'href="/inventory/{i.pk}/view/"', dash)
        cal = self.client.get("/planner/").content.decode()
        self.assertIn(f'href="/planner/events/{e.pk}/view/"', cal)
        agenda = self.client.get("/planner/agenda/").content.decode()
        self.assertIn(f'href="/planner/events/{e.pk}/view/"', agenda); self.assertIn(f'href="/inventory/{i.pk}/view/"', agenda)


class EfficiencyTests(DetailBase):
    def test_query_count_does_not_grow_with_the_amount_of_related_data(self):
        def count():
            with CaptureQueriesContext(connection) as q:
                self.assertEqual(self.get("property").status_code, 200)
            return len(q)
        with use_account(self.acme):                       # make every section non-empty first: an empty section costs one query, a full one two
            ContactProperty.objects.create(contact=self.objs["contact"], property=self.objs["property"])
        small = count()
        with use_account(self.acme):
            for i in range(40):
                Unit.objects.create(property=self.objs["property"], name=f"U{i}")
                InventoryItem.objects.create(property=self.objs["property"], name=f"I{i}", category="hvac")
                t = MaintenanceTask.objects.create(property=self.objs["property"], title=f"T{i}", start_date=D(2030, 1, 1 + i % 28), recurrence_rule="")
                ensure_first_occurrence(t)
                Event.objects.create(title=f"E{i}", starts_on=D(2030, 1, 1 + i % 28), property=self.objs["property"])
                c = Contact.objects.create(name=f"C{i}"); ContactProperty.objects.create(contact=c, property=self.objs["property"])
        self.assertEqual(small, count())


class PresentationTests(DetailBase):
    def test_the_records_own_name_leads_the_field_list(self):
        for key, label in (("unit", "Name"), ("item", "Name"), ("task", "Title"), ("event", "Title"), ("property", "Name"), ("contact", "Name")):
            first = next(iter(table(self.get(key))))
            self.assertEqual(first, label, key)

    def test_values_read_like_a_person_would_say_them(self):
        with use_account(self.acme):
            it = self.objs["item"]; it.cost = Decimal("6800"); it.purchase_date = D(2024, 3, 1); it.warranty_expires_on = D(2027, 3, 1); it.save()
            ev = self.objs["event"]; ev.start_time = None; ev.save()
        t = table(self.get("item"))
        self.assertEqual((t["Cost"], t["Purchase date"], t["Warranty expires on"]), ("$6,800.00", "Mar 1, 2024", "Mar 1, 2027"))
        with use_account(self.acme):
            ev = self.objs["event"]; ev.start_time = __import__("datetime").time(13, 5); ev.end_time = __import__("datetime").time(14, 30); ev.save()
        e = table(self.get("event"))
        self.assertEqual((e["Start time"], e["End time"]), ("1:05 PM", "2:30 PM"))

    def test_labels_with_trailing_digits_are_spaced(self):
        t = table(self.get("property"))
        self.assertIn("Address line 1", t); self.assertIn("Address line 2", t); self.assertNotIn("Address line1", t)
