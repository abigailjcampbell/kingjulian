import re
from datetime import date as D
from unittest import mock

from django.urls import get_resolver

from accounts.models import Membership, User
from archive.registry import BY_MODEL, KINDS
from audit.models import ActivityLog
from contacts.models import Contact, ContactProperty
from core.tenancy import use_account
from core.tests.base import PW
from maintenance.reminders import generate_reminders
from properties.models import Property, Unit

from attachments.tests.base import png
from .base import ArchiveWorld

XSS = "<script>alert(1)</script>"


def rows(response):
    return response.context["page"].object_list


class RegistryTests(ArchiveWorld):
    def test_every_archivable_record_type_is_registered(self):
        """Fails the day someone adds an *_archive URL for a model this page doesn't know about."""
        models = set()

        def walk(patterns):
            for p in patterns:
                if hasattr(p, "url_patterns"):
                    walk(p.url_patterns)
                elif getattr(p, "name", None) and p.name.endswith("_archive"):
                    models.add(p.callback.view_class.model)
        walk(get_resolver().url_patterns)
        self.assertEqual(models, set(BY_MODEL), "archive.registry.KINDS must list every model that has an *_archive view")

    def test_kind_keys_are_unique_and_match_url_safe_names(self):
        self.assertEqual(sorted(KINDS), ["contact", "event", "item", "property", "task", "unit"])


class ListTests(ArchiveWorld):
    def setUp(self):
        self.login(self.alice)
        self.objs = self.make_all()

    def test_lists_every_type_newest_first_with_who_archived_it(self):
        order = ["event", "contact", "item", "unit", "task", "property"]
        for key in order:
            self.archive(self.objs[key], user=self.alice if key != "contact" else self.carol)
        r = self.client.get("/archive/")
        self.assertEqual([x["kind"].key for x in rows(r)], list(reversed(order)))
        by = {x["kind"].key: x["by"] for x in rows(r)}
        self.assertEqual((by["property"], by["contact"]), ("alice@acme.test", "carol@both.test"))
        first = rows(r)[-1]                                     # the event, archived first
        self.assertEqual((first["name"], first["kind"].label, first["where"]), ("Walkthrough", "Event", "Birch Lodge"))
        for text in ("Birch Lodge", "Inventory item", "Maintenance task", "Roofers Inc", "Restore</button>"):
            self.assertContains(r, text)

    def test_who_is_system_when_nobody_was_signed_in_and_a_dash_when_unknown(self):
        self.archive(self.objs["contact"], user=None)                              # e.g. a script
        with use_account(self.acme):
            type(self.objs["event"]).objects.filter(pk=self.objs["event"].pk).update(archived_at=D(2026, 1, 1))    # no log entry at all
        by = {x["kind"].key: x["by"] for x in rows(self.client.get("/archive/"))}
        self.assertEqual((by["contact"], by["event"]), ("System", "—"))

    def test_active_records_are_not_listed(self):
        self.archive(self.objs["contact"])
        names = [x["name"] for x in rows(self.client.get("/archive/"))]
        self.assertEqual(names, ["Zed Roofer"])

    def test_type_filter_counts_and_search(self):
        for key in ("property", "unit", "item", "contact"):
            self.archive(self.objs[key])
        r = self.client.get("/archive/?type=contact")
        self.assertEqual([x["name"] for x in rows(r)], ["Zed Roofer"])
        self.assertEqual({k: n for k, _, n in r.context["type_options"]}, {"property": 1, "unit": 1, "item": 1, "contact": 1, "task": 0, "event": 0})
        self.assertContains(r, "All types (4)"); self.assertContains(r, '<option value="contact" selected>')
        names = lambda q: sorted(x["name"] for x in rows(self.client.get("/archive/?q=" + q)))
        self.assertEqual(names("SP-1"), ["Sump Pump"])                              # serial number
        self.assertEqual(names("roofers"), ["Zed Roofer"])                          # company
        self.assertEqual(names("birch"), ["Birch Lodge", "Cellar", "Sump Pump"])    # by property name, across types
        self.assertEqual(names("ogden"), ["Birch Lodge"]); self.assertEqual(names("zzz"), [])
        self.assertEqual(sorted(x["name"] for x in rows(self.client.get("/archive/?type=property&q=birch"))), ["Birch Lodge"])

    def test_junk_parameters_never_break_the_page(self):
        self.archive(self.objs["contact"])
        for q in ("?type=garbage", "?type=", "?page=abc", "?page=999", "?page=-1", "?q=%25", "?q=_", "?q=" + "x" * 5000, "?type=property&page=2"):
            self.assertEqual(self.client.get("/archive/" + q).status_code, 200, q)
        self.assertEqual(len(rows(self.client.get("/archive/?type=garbage"))), 1)      # unknown type = all types
        self.assertEqual(self.client.get("/archive/?page=999").context["page"].number, 1)

    def test_pagination_keeps_the_filters(self):
        with use_account(self.acme):
            for i in range(30):
                Property.objects.create(name=f"Bulk {i:02d}").archive()
        r = self.client.get("/archive/?type=property")
        self.assertEqual((len(rows(r)), r.context["page"].paginator.count), (25, 30))
        self.assertContains(r, "type=property&amp;page=2")
        self.assertEqual(len(rows(self.client.get("/archive/?type=property&page=2"))), 5)

    def test_cap_is_announced_not_silent(self):
        with use_account(self.acme):
            for i in range(3):
                Property.objects.create(name=f"Cap {i}").archive()
        with mock.patch("archive.views.MAX_PER_KIND", 2):
            self.assertContains(self.client.get("/archive/"), "most recent 1,000")
            self.assertEqual(len(rows(self.client.get("/archive/"))), 2)
        self.assertNotContains(self.client.get("/archive/"), "most recent 1,000")

    def test_other_accounts_never_appear_in_rows_counts_or_search(self):
        with use_account(self.beta):
            Property.objects.create(name="BETA Plaza").archive()
        self.archive(self.objs["contact"])
        r = self.client.get("/archive/?q=beta")
        self.assertEqual(rows(r), []); self.assertNotContains(r, "BETA Plaza")
        self.assertEqual(self.client.get("/archive/").context["total"], 1)
        self.login(self.bella); self.assertEqual([x["name"] for x in rows(self.client.get("/archive/"))], ["BETA Plaza"])

    def test_only_owners_and_managers_can_see_it(self):
        mgr = User.objects.create_user("mgr@acme.test", PW); Membership.objects.create(user=mgr, account=self.acme, role="manager")
        self.login(mgr); self.assertEqual(self.client.get("/archive/").status_code, 200)
        self.login(self.vic); self.assertEqual(self.client.get("/archive/").status_code, 403)
        self.assertNotContains(self.client.get("/"), 'href="/archive/"')
        self.client.logout(); r = self.client.get("/archive/")
        self.assertEqual(r.status_code, 302); self.assertIn("/accounts/login/", r["Location"])
        self.login(self.alice); self.assertContains(self.client.get("/"), 'href="/archive/"')

    def test_user_supplied_text_is_escaped(self):
        with use_account(self.acme):
            Property.objects.create(name=XSS, city="<b>x</b>").archive()
            Contact.objects.create(name=XSS, company=XSS).archive()
        r = self.client.get("/archive/")
        self.assertNotContains(r, XSS); self.assertNotContains(r, "<b>x</b>"); self.assertContains(r, "&lt;script&gt;")

    def test_empty_state(self):
        self.assertContains(self.client.get("/archive/"), "Nothing is archived.")
        self.archive(self.objs["contact"]); self.assertContains(self.client.get("/archive/?q=zzz"), "Nothing archived matches.")

    def test_query_count_does_not_grow_with_the_number_of_archived_rows(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def count():
            with CaptureQueriesContext(connection) as q:
                self.assertEqual(self.client.get("/archive/").status_code, 200)
            return len(q)
        self.archive(self.objs["contact"]); small = count()
        with use_account(self.acme):
            for i in range(20):
                u = Unit.objects.create(property=self.objs["property"], name=f"U{i}"); u.archive()
                Contact.objects.create(name=f"C{i}").archive()
        self.assertEqual(small, count())


class DetailTests(ArchiveWorld):
    def setUp(self):
        self.login(self.alice)
        self.objs = self.make_all()

    def page(self, key):
        self.archive(self.objs[key], user=self.alice)
        return self.client.get(f"/archive/{key}/{self.objs[key].pk}/")

    def table(self, r):
        return dict(re.findall(r"<th[^>]*>(.*?)</th><td>(.*?)</td>", r.content.decode(), flags=re.S))

    def test_fields_are_shown_as_people_read_them(self):
        t = self.table(self.page("item"))
        self.assertEqual((t["Name"], t["Category"], t["Condition"], t["Property"], t["Unit"], t["Serial number"], t["Quantity"]),
                         ("Sump Pump", "Plumbing", "Good", "Birch Lodge", "Birch Lodge / Cellar", "SP-1", "1"))
        self.assertEqual((t["Cost"], t["Warranty expires on"]), ("—", "—"))                    # blanks are dashes, not "None"
        for hidden in ("Id", "Account", "Created at", "Updated at", "Archived at"):
            self.assertNotIn(hidden, t)

    def test_tasks_contacts_and_properties_get_friendly_rows(self):
        t = self.table(self.page("task"))
        self.assertEqual((t["Repeats"], t["Remind days before"]), ("Yearly", "7, 1"))
        self.assertNotIn("Recurrence rule", t); self.assertNotIn("Schedule mode", t)
        with use_account(self.acme):
            ContactProperty.objects.create(contact=self.objs["contact"], property=self.maple); ContactProperty.objects.create(contact=self.objs["contact"], property=self.oak)
        self.assertEqual(self.table(self.page("contact"))["Properties"], "Maple Duplex; Oak Cottage")
        self.assertEqual(self.table(self.page("property"))["Property type"], "Single family")

    def test_who_when_and_history_are_shown(self):
        r = self.page("property")
        self.assertContains(r, "by alice@acme.test"); self.assertContains(r, "<h2>History"); self.assertContains(r, "Archived property “Birch Lodge”")

    def test_files_are_visible_and_downloadable_but_nothing_can_be_uploaded(self):
        att = self.store(self.objs["property"], "plans.png", png())
        r = self.page("property")
        self.assertContains(r, "plans.png"); self.assertContains(r, f'href="/files/{att.pk}/download/"')
        self.assertNotContains(r, 'type="file"'); self.assertNotContains(r, "Upload</button>")
        self.assertEqual(self.client.get(f"/files/{att.pk}/download/").status_code, 200)

    def test_a_record_that_is_active_again_redirects_to_its_live_page(self):
        live = {"property": "/properties/{}/view/", "unit": "/properties/units/{}/view/", "item": "/inventory/{}/view/", "contact": "/contacts/{}/view/",
                "task": "/maintenance/tasks/{}/view/", "event": "/planner/events/{}/view/"}
        for key, url in live.items():
            r = self.client.get(f"/archive/{key}/{self.objs[key].pk}/")
            self.assertRedirects(r, url.format(self.objs[key].pk), fetch_redirect_response=False)

    def test_unknown_missing_and_foreign_records_are_404(self):
        self.archive(self.objs["contact"])
        theirs = Property.all_tenants.get(pk=self.lake.pk)
        with use_account(self.beta):
            theirs.archive()
        for url in ("/archive/nonsense/1/", "/archive/contact/999999/", f"/archive/property/{self.lake.pk}/", "/archive/occurrence/1/"):
            self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_viewers_and_strangers_are_refused(self):
        self.archive(self.objs["contact"]); url = f"/archive/contact/{self.objs['contact'].pk}/"
        self.login(self.vic); self.assertEqual(self.client.get(url).status_code, 403)
        self.login(self.bella); self.assertEqual(self.client.get(url).status_code, 404)
        self.client.logout(); self.assertEqual(self.client.get(url).status_code, 302)

    def test_parent_still_archived_replaces_the_restore_button_with_an_explanation(self):
        self.archive(self.objs["property"]); self.archive(self.objs["unit"])
        r = self.client.get(f"/archive/unit/{self.objs['unit'].pk}/")
        self.assertContains(r, "Restore the property “Birch Lodge” first.")
        self.assertContains(r, f'href="/archive/property/{self.objs["property"].pk}/"')          # a way to go and do it
        self.assertNotContains(r, "Restore unit</button>")

    def test_escaping(self):
        with use_account(self.acme):
            p = Property.objects.create(name=XSS, notes=XSS); p.archive()
        r = self.client.get(f"/archive/property/{p.pk}/")
        self.assertNotContains(r, XSS); self.assertContains(r, "&lt;script&gt;")


class RestoreTests(ArchiveWorld):
    def setUp(self):
        self.login(self.alice)
        self.objs = self.make_all()

    @staticmethod
    def snapshot_name(obj):
        return getattr(obj, "title", None) or obj.name

    def restore(self, key, **extra):
        return self.client.post(f"/archive/{key}/{self.objs[key].pk}/restore/", extra)

    def test_every_type_comes_back_exactly_as_it_was(self):
        live_pages = {"property": "/properties/", "unit": "/properties/units/", "item": "/inventory/", "contact": "/contacts/",
                      "task": "/maintenance/tasks/", "event": "/planner/events/"}
        for key, obj in self.objs.items():
            before = self.snapshot(self.fresh(obj))
            self.archive(obj, user=self.alice)

            list_url = f"/properties/{obj.property_id}/units/" if key == "unit" else live_pages[key]      # units are listed under their property
            self.assertNotContains(self.client.get(list_url, HTTP_HX_REQUEST="true"), self.snapshot_name(obj))
            r = self.restore(key)
            
            self.assertRedirects(r, "/archive/", fetch_redirect_response=False)
            after = self.fresh(obj)
            self.assertIsNone(after.archived_at, key); self.assertEqual(self.snapshot(after), before, key)
            self.assertEqual(self.client.get(f"{live_pages[key]}{obj.pk}/").status_code, 200, key)        # its edit page works again
        self.assertEqual(self.client.get("/archive/").context["total"], 0)

    def test_the_restore_is_confirmed_and_recorded_in_the_activity_log(self):
        self.archive(self.objs["item"], user=self.alice)
        r = self.client.post(f"/archive/item/{self.objs['item'].pk}/restore/", follow=True)
        self.assertContains(r, "Restored inventory item “Sump Pump”.")
        with use_account(self.acme):
            e = ActivityLog.objects.filter(action="restored").get()
        self.assertEqual((e.summary, e.actor, e.target_type, e.target_id), ("Restored inventory item “Sump Pump”", self.alice, "inventoryitem", self.objs["item"].pk))

    def test_next_cannot_redirect_off_site(self):
        for bad in ("https://evil.example/", "//evil.example/x", "javascript:alert(1)", "http://testserver.evil.com/"):
            self.archive(self.objs["contact"])
            self.assertEqual(self.restore("contact", next=bad)["Location"], "/archive/", bad)
        self.archive(self.objs["contact"])
        self.assertEqual(self.restore("contact", next="/contacts/")["Location"], "/contacts/")

    def test_restoring_twice_is_harmless(self):
        self.archive(self.objs["contact"])
        self.restore("contact")
        r = self.client.post(f"/archive/contact/{self.objs['contact'].pk}/restore/", follow=True)
        self.assertContains(r, "already active")
        with use_account(self.acme):
            self.assertEqual(ActivityLog.objects.filter(action="restored").count(), 1)

    def test_methods_roles_and_tenants(self):
        self.archive(self.objs["contact"]); url = f"/archive/contact/{self.objs['contact'].pk}/restore/"
        self.assertEqual(self.client.get(url).status_code, 405)
        self.login(self.vic); self.assertEqual(self.client.post(url).status_code, 403)
        self.login(self.bella); self.assertEqual(self.client.post(url).status_code, 404)
        self.client.logout(); self.assertEqual(self.client.post(url).status_code, 302)
        self.assertIsNotNone(self.fresh(self.objs["contact"]).archived_at)
        self.login(self.alice); self.assertEqual(self.client.post("/archive/nonsense/1/restore/").status_code, 404)

    def test_children_cannot_come_back_before_their_parents(self):
        self.archive(self.objs["property"])
        for key in ("unit", "item", "task", "event"):
            self.archive(self.objs[key])
            r = self.client.post(f"/archive/{key}/{self.objs[key].pk}/restore/", follow=True)
            self.assertContains(r, "Restore the property “Birch Lodge” first.", msg_prefix=key)
            self.assertEqual(r.redirect_chain[-1][0], f"/archive/{key}/{self.objs[key].pk}/", key)
            self.assertIsNotNone(self.fresh(self.objs[key]).archived_at, key)
        self.restore("property")
        for key in ("unit", "item", "task", "event"):
            self.assertEqual(self.restore(key).status_code, 302)
            self.assertIsNone(self.fresh(self.objs[key]).archived_at, key)

    def test_optional_links_also_block_so_forms_never_silently_drop_them(self):
        with use_account(self.acme):
            task = self.objs["task"]; task.item = self.objs["item"]; task.assigned_contact = self.objs["contact"]; task.save()
            ev = self.objs["event"]; ev.contact = self.objs["contact"]; ev.save()
        self.archive(self.objs["item"]); self.archive(self.objs["contact"]); self.archive(self.objs["task"]); self.archive(self.objs["event"])
        r = self.client.post(f"/archive/task/{self.objs['task'].pk}/restore/", follow=True)
        self.assertContains(r, "Restore the inventory item “Sump Pump” first."); self.assertContains(r, "Restore the contact “Zed Roofer” first.")
        r = self.client.post(f"/archive/event/{self.objs['event'].pk}/restore/", follow=True)
        self.assertContains(r, "Restore the contact “Zed Roofer” first.")
        self.restore("item"); self.restore("contact")
        self.assertEqual(self.restore("task").status_code, 302); self.assertEqual(self.restore("event").status_code, 302)

    def test_a_name_clash_is_explained_and_changes_nothing(self):
        self.archive(self.objs["unit"])
        with use_account(self.acme):
            Unit.objects.create(property=self.objs["property"], name="Cellar")            # allowed: the old name was freed by archiving
        r = self.client.post(f"/archive/unit/{self.objs['unit'].pk}/restore/", follow=True)
        self.assertContains(r, "An active record already uses the same name")
        self.assertIsNotNone(self.fresh(self.objs["unit"]).archived_at)
        self.assertEqual(self.client.get("/archive/").status_code, 200)                    # the transaction is not poisoned

    def test_contact_links_survive_an_archive_and_restore(self):
        with use_account(self.acme):
            ContactProperty.objects.create(contact=self.objs["contact"], property=self.maple)
        self.archive(self.objs["contact"]); self.restore("contact")
        self.assertEqual(self.client.get(f"/contacts/{self.objs['contact'].pk}/").context["form"].fields["properties"].initial, [self.maple.pk])

    def test_a_restored_task_gets_its_reminders_back(self):
        from datetime import timedelta
        from django.utils import timezone
        from maintenance.services import ensure_first_occurrence
        today = timezone.localdate()
        with use_account(self.acme):
            t = self.objs["task"]; t.start_date = today + timedelta(days=3); t.save(); ensure_first_occurrence(t)
        self.archive(t)
        with use_account(self.acme):
            self.assertEqual(generate_reminders(today)["created"], 0)
        self.restore("task")
        with use_account(self.acme):
            self.assertEqual(generate_reminders(today)["created"], 1)

    def test_files_survive_an_archive_and_restore(self):
        att = self.store(self.objs["property"], "plans.png", png())
        self.archive(self.objs["property"]); self.restore("property")
        self.assertEqual(self.client.get(f"/files/{att.pk}/download/").status_code, 200)
        self.assertContains(self.client.get(f"/properties/{self.objs['property'].pk}/view/"))

    def test_restoring_in_one_account_leaves_a_same_named_record_elsewhere_alone(self):
        with use_account(self.beta):
            theirs = Property.objects.create(name="Birch Lodge"); theirs.archive()
        self.archive(self.objs["property"]); self.restore("property")
        self.assertIsNotNone(Property.all_tenants.get(pk=theirs.pk).archived_at)


class UndoLinkTests(ArchiveWorld):
    def setUp(self):
        self.login(self.alice)
        self.objs = self.make_all()

    def test_archiving_offers_an_undo_link_for_every_type(self):
        archive_urls = {"property": "/properties/{}/archive/", "unit": "/properties/units/{}/archive/", "item": "/inventory/{}/archive/",
                        "contact": "/contacts/{}/archive/", "task": "/maintenance/tasks/{}/archive/", "event": "/planner/events/{}/archive/"}
        for key, url in archive_urls.items():
            pk = self.objs[key].pk
            r = self.client.post(url.format(pk), follow=True)
            self.assertContains(r, f'<a href="/archive/{key}/{pk}/">Undo</a>', msg_prefix=key)

    def test_full_round_trip_through_the_undo_link(self):
        pk = self.objs["contact"].pk
        self.client.post(f"/contacts/{pk}/archive/")
        table = lambda: self.client.get("/contacts/", HTTP_HX_REQUEST="true")          # the results fragment: no flash message
        self.assertNotContains(table(), "Zed Roofer")
        page = self.client.get(f"/archive/contact/{pk}/"); self.assertEqual(page.status_code, 200)
        self.client.post(f"/archive/contact/{pk}/restore/")
        self.assertContains(table(), "Zed Roofer")

    def test_a_hostile_name_cannot_inject_html_through_the_message(self):
        with use_account(self.acme):
            p = Property.objects.create(name='<img src=x onerror=alert(1)>')
        r = self.client.post(f"/properties/{p.pk}/archive/", follow=True)
        self.assertNotContains(r, "<img src=x onerror"); self.assertContains(r, "&lt;img src=x onerror=alert(1)&gt;"); self.assertContains(r, "Undo</a>")

    def test_archiving_still_works_if_the_archive_page_is_unavailable(self):
        with mock.patch("archive.registry.detail_url", side_effect=ImportError):
            r = self.client.post(f"/contacts/{self.objs['contact'].pk}/archive/", follow=True)
        self.assertContains(r, "Archived Zed Roofer."); self.assertNotContains(r, "Undo")
        self.assertIsNotNone(self.fresh(self.objs["contact"]).archived_at)
