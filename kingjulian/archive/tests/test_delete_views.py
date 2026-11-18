import re

from django.test import Client

from accounts.models import Membership, User
from attachments.models import Attachment
from attachments.tests.base import png
from audit.models import ActivityLog
from contacts.models import Contact
from core.tenancy import use_account
from core.tests.base import PW
from planner.models import Event
from properties.models import Property, Unit

from .test_delete import XSS, DeleteWorld


class ConfirmBase(DeleteWorld):
    def setUp(self):
        super().setUp()
        self.login(self.alice)
        self.mgr = User.objects.create_user("mgr@acme.test", PW)
        Membership.objects.create(user=self.mgr, account=self.acme, role=Membership.Role.MANAGER)

    def url(self, kind, obj):
        return f"/archive/{kind}/{obj.pk}/delete/"

    def confirm(self, kind, obj, word="DELETE", token=None, **extra):
        if token is None:
            token = self.client.get(self.url(kind, obj)).context["plan"].token
        return self.client.post(self.url(kind, obj), {"confirm": word, "token": token}, **extra)

    def table_names(self, url):
        return self.client.get(url, HTTP_HX_REQUEST="true").content.decode()


class ConfirmPageTests(ConfirmBase):
    def test_the_page_says_exactly_what_will_be_destroyed_and_changed(self):
        c, t, ev = self.contact_with_everything(); c = self.archived(c)
        r = self.client.get(self.url("contact", c))
        self.assertEqual(r.status_code, 200)
        for text in ("Permanently delete “Zed Roofer”?", "cannot be undone", "Files: COI.pdf, license.png", "2 files (", "the contact “Zed Roofer” itself",
                     "1 maintenance task will keep existing but no longer have this contact assigned", "1 event will keep existing",
                     "2 contact-to-property links will be removed", "The activity log keeps a record", f"Type DELETE to confirm",
                     'name="confirm"', 'name="token"', f'action="/archive/contact/{c.pk}/restore/"'):
            self.assertContains(r, text)
        self.assertNotContains(r, "can't be deleted yet")

    def test_a_task_page_counts_its_history(self):
        t = self.archived(self.task_with_history())
        self.assertContains(self.client.get(self.url("task", t)), "3 jobs of history (2 completed, $725.50 of recorded cost)")

    def test_a_blocked_record_shows_what_is_in_the_way_and_offers_no_delete_form(self):
        objs = self.make_all(); prop = self.archived(objs["property"])
        r = self.client.get(self.url("property", prop))
        for text in ("It can't be deleted yet", "1 unit", "1 inventory item", "1 maintenance task", "1 event", f'href="/properties/units/{objs["unit"].pk}/view/"'):
            self.assertContains(r, text)
        self.assertNotContains(r, 'name="confirm"'); self.assertNotContains(r, "Permanently delete</button>")
        with use_account(self.acme):
            for i in range(8):
                Unit.objects.create(property=prop, name=f"U{i}")
        self.assertContains(self.client.get(self.url("property", prop)), "and 4 more")                 # 9 units, 5 shown

    def test_viewing_the_page_never_changes_anything(self):
        c, t, ev = self.contact_with_everything(); c = self.archived(c)
        for _ in range(3):
            self.client.get(self.url("contact", c))
        self.assertTrue(Contact.all_tenants.filter(pk=c.pk).exists()); self.assertEqual(Attachment.all_tenants.count(), 2)

    def test_active_records_are_sent_back_to_their_live_page_with_an_explanation(self):
        objs = self.make_all()
        live = {"property": "/properties/{}/view/", "unit": "/properties/units/{}/view/", "item": "/inventory/{}/view/", "contact": "/contacts/{}/view/",
                "task": "/maintenance/tasks/{}/view/", "event": "/planner/events/{}/view/"}
        for key, pattern in live.items():
            r = self.client.get(self.url(key, objs[key]), follow=True)
            self.assertEqual(r.redirect_chain[-1][0], pattern.format(objs[key].pk), key)
            self.assertContains(r, "Only archived records can be permanently deleted", msg_prefix=key)

    def test_unknown_types_are_404_missing_and_foreign_records_are_reported_neutrally(self):
        self.assertEqual(self.client.get("/archive/nonsense/1/delete/").status_code, 404)
        r = self.client.get("/archive/contact/999999/delete/", follow=True)
        self.assertContains(r, "That record no longer exists."); self.assertEqual(r.redirect_chain[-1][0], "/archive/")
        with use_account(self.beta):
            theirs = Contact.objects.create(name="Theirs"); theirs.archive()
        r = self.client.post(self.url("contact", theirs), {"confirm": "DELETE", "token": "x"}, follow=True)
        self.assertContains(r, "That record no longer exists."); self.assertTrue(Contact.all_tenants.filter(pk=theirs.pk).exists())

    def test_only_owners_may_open_or_submit_it(self):
        c, t, ev = self.contact_with_everything(); c = self.archived(c)
        token = self.client.get(self.url("contact", c)).context["plan"].token
        for user in (self.mgr, self.vic):
            self.login(user)
            self.assertEqual(self.client.get(self.url("contact", c)).status_code, 403, user.email)
            self.assertEqual(self.client.post(self.url("contact", c), {"confirm": "DELETE", "token": token}).status_code, 403, user.email)
        self.client.logout(); r = self.client.get(self.url("contact", c))
        self.assertEqual(r.status_code, 302); self.assertIn("/accounts/login/", r["Location"])
        self.assertTrue(Contact.all_tenants.filter(pk=c.pk).exists())

    def test_delete_links_are_only_shown_to_owners(self):
        c, t, ev = self.contact_with_everything(); c = self.archived(c)
        self.assertContains(self.client.get("/archive/"), f'href="/archive/contact/{c.pk}/delete/"')
        self.assertContains(self.client.get(f"/archive/contact/{c.pk}/"), "Delete permanently")
        self.login(self.mgr)
        self.assertNotContains(self.client.get("/archive/"), "/delete/"); self.assertNotContains(self.client.get(f"/archive/contact/{c.pk}/"), "Delete permanently")

    def test_user_supplied_text_is_escaped(self):
        with use_account(self.acme):
            p = Property.objects.create(name=XSS); Unit.objects.create(property=p, name=XSS)
        p = self.archived(p)
        r = self.client.get(self.url("property", p))
        self.assertNotContains(r, XSS); self.assertContains(r, "&lt;script&gt;")


class ConfirmingTests(ConfirmBase):
    def test_wrong_or_missing_confirmation_deletes_nothing_and_explains(self):
        c, t, ev = self.contact_with_everything(); c = self.archived(c)
        token = self.client.get(self.url("contact", c)).context["plan"].token
        for word in ("", "yes", "DELET", "DELETE ME", "delete it", "Delete1"):
            r = self.confirm("contact", c, word=word, token=token)
            self.assertEqual(r.status_code, 200, word); self.assertContains(r, "Type DELETE to confirm.")
        r = self.client.post(self.url("contact", c), {"token": token})                        # no confirm field at all
        self.assertContains(r, "Type DELETE to confirm.")
        self.assertTrue(Contact.all_tenants.filter(pk=c.pk).exists()); self.assertEqual(Attachment.all_tenants.count(), 2)

    def test_case_and_surrounding_spaces_are_forgiven(self):
        for word in ("delete", "  DELETE  ", "Delete"):
            c = self.archived(self.make_contact(word)); self.assertEqual(self.confirm("contact", c, word=word).status_code, 302, repr(word))
            self.assertFalse(Contact.all_tenants.filter(pk=c.pk).exists(), repr(word))

    def make_contact(self, name):
        with use_account(self.acme):
            return Contact.objects.create(name=f"Temp {name.strip()}")

    def test_a_blind_post_without_the_page_is_refused(self):
        c, t, ev = self.contact_with_everything(); c = self.archived(c)
        for extra in ({"confirm": "DELETE"}, {"confirm": "DELETE", "token": "not-the-token"}, {"confirm": "DELETE", "token": ""}):
            r = self.client.post(self.url("contact", c), extra, follow=True)
            self.assertContains(r, "changed while you were looking"); self.assertEqual(r.redirect_chain[-1][0], self.url("contact", c))
        self.assertTrue(Contact.all_tenants.filter(pk=c.pk).exists())

    def test_if_the_plan_changes_after_the_page_was_shown_the_user_must_look_again(self):
        c, t, ev = self.contact_with_everything(); c = self.archived(c)
        stale = self.client.get(self.url("contact", c)).context["plan"].token
        with use_account(self.acme):
            Event.objects.create(title="Added meanwhile", starts_on=self.today, contact=c)
        r = self.confirm("contact", c, token=stale, follow=True)
        self.assertContains(r, "changed while you were looking"); self.assertTrue(Contact.all_tenants.filter(pk=c.pk).exists())
        self.assertContains(r, "2 events will keep existing")                                  # the fresh page shows the new reality
        self.assertEqual(self.confirm("contact", c).status_code, 302)                           # confirming the fresh page works
        self.assertFalse(Contact.all_tenants.filter(pk=c.pk).exists())

    def test_the_whole_flow_for_a_contact_with_files(self):
        c, t, ev = self.contact_with_everything(); c = self.archived(c)
        paths = self.stored_paths(); file_id = Attachment.all_tenants.first().pk
        with self.captureOnCommitCallbacks(execute=True):
            r = self.confirm("contact", c, follow=True)
        self.assertEqual(r.redirect_chain[-1][0], "/archive/"); self.assertContains(r, "Permanently deleted contact “Zed Roofer”.")
        self.assertNotContains(self.client.get("/archive/", HTTP_HX_REQUEST="true"), "Zed Roofer")
        self.assertFalse(any(self.on_disk(p) for p in paths)); self.assertEqual(self.client.get(f"/files/{file_id}/download/").status_code, 404)
        feed = self.client.get("/activity/?action=deleted")
        self.assertContains(feed, "Permanently deleted contact"); self.assertContains(feed, "alice@acme.test"); self.assertContains(feed, "COI.pdf, license.png")
        entry = ActivityLog.all_tenants.get(action="deleted")
        self.assertContains(self.client.get(f"/activity/{entry.pk}/", follow=True), "That record no longer exists.")        # jump: gone, said politely

    def test_the_whole_flow_for_a_task(self):
        t = self.archived(self.task_with_history())
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.confirm("task", t).status_code, 302)
        self.assertEqual((TaskOccurrenceCount.of(t), Attachment.all_tenants.count()), (0, 0))
        self.assertContains(self.client.get("/exports/history.csv"), "")                      # exports still work afterwards
        self.assertNotContains(self.client.get("/exports/history.csv"), "Gutter cleaning")

    def test_double_submission_is_harmless(self):
        c, t, ev = self.contact_with_everything(); c = self.archived(c)
        token = self.client.get(self.url("contact", c)).context["plan"].token
        self.client.post(self.url("contact", c), {"confirm": "DELETE", "token": token})
        r = self.client.post(self.url("contact", c), {"confirm": "DELETE", "token": token}, follow=True)
        self.assertContains(r, "That record no longer exists."); self.assertEqual(r.status_code, 200)

    def test_a_blocked_record_cannot_be_deleted_even_with_the_right_word(self):
        objs = self.make_all(); prop = self.archived(objs["property"])
        r = self.confirm("property", prop)
        self.assertEqual(r.status_code, 200); self.assertContains(r, "can't be deleted yet")
        self.assertTrue(Property.all_tenants.filter(pk=prop.pk).exists()); self.assertTrue(Unit.all_tenants.filter(pk=objs["unit"].pk).exists())

    def test_csrf_is_enforced(self):
        c, t, ev = self.contact_with_everything(); c = self.archived(c)
        strict = Client(enforce_csrf_checks=True); strict.force_login(self.alice)
        self.assertEqual(strict.post(self.url("contact", c), {"confirm": "DELETE", "token": "x"}).status_code, 403)
        self.assertTrue(Contact.all_tenants.filter(pk=c.pk).exists())

    def test_deleting_in_one_account_never_touches_another(self):
        with use_account(self.beta):
            theirs = Contact.objects.create(name="Zed Roofer"); theirs.archive()
        c, t, ev = self.contact_with_everything(); c = self.archived(c)
        self.confirm("contact", c)
        self.assertTrue(Contact.all_tenants.filter(pk=theirs.pk).exists())


class TaskOccurrenceCount:
    @staticmethod
    def of(task):
        from maintenance.models import TaskOccurrence
        return TaskOccurrence.all_tenants.filter(task_id=task.pk).count()


class FileListTests(ConfirmBase):
    def test_many_files_are_summarised_after_ten(self):
        with use_account(self.acme):
            c = Contact.objects.create(name="Paper Hoarder")
        for i in range(13):
            self.store(c, f"scan-{i:02d}.png", png())
        c = self.archived(c)
        r = self.client.get(self.url("contact", c))
        self.assertContains(r, "scan-00.png, scan-01.png"); self.assertContains(r, "scan-09.png and 3 more"); self.assertNotContains(r, "scan-12.png")
        self.confirm("contact", c)
        entry = ActivityLog.all_tenants.get(action="deleted")
        self.assertTrue(entry.changes["Files"][1].endswith("and 3 more"))
