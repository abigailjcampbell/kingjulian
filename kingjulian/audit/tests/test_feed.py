from datetime import date as D

from accounts.models import Membership, User
from audit.models import ActivityLog
from core.tenancy import use_account
from core.tests.base import PW
from properties.models import Property

from attachments.tests.base import png
from .base import AuditWorld

XSS = "<script>alert(1)</script>"


class FeedBase(AuditWorld):
    def setUp(self):
        super().setUp()
        self.mgr = User.objects.create_user("mgr@acme.test", PW)
        Membership.objects.create(user=self.mgr, account=self.acme, role=Membership.Role.MANAGER)
        self.login(self.alice)
        self.client.post("/properties/new/", {"name": "Birch Lodge", "property_type": "single_family", "city": "Ogdenville"})
        self.birch = Property.all_tenants.get(name="Birch Lodge")
        self.client.post(f"/properties/{self.birch.pk}/", {"name": "Birch Lodge", "property_type": "single_family", "city": "Springfield"})
        self.login(self.carol)
        self.client.post("/contacts/new/", {"name": "Zed Roofer", "role": "contractor"})
        with use_account(self.beta):
            Property.objects.create(name="BETA Plaza")
        self.client.get("/")                       # show (and so clear) the pending "Saved ..." flash message, as a real next page load would

    def summaries(self, q="", user=None):
        if user:
            self.login(user)
        return [e.summary for e in self.client.get("/activity/" + q).context["object_list"]]


class AccessTests(FeedBase):
    def test_owners_and_managers_see_it_viewers_and_strangers_do_not(self):
        for user, code in ((self.alice, 200), (self.mgr, 200), (self.vic, 403)):
            self.login(user); self.assertEqual(self.client.get("/activity/").status_code, code, user.email)
        self.client.logout(); r = self.client.get("/activity/")
        self.assertEqual(r.status_code, 302); self.assertIn("/accounts/login/", r["Location"])

    def test_nav_link_is_for_writers_only(self):
        self.login(self.alice); self.assertContains(self.client.get("/"), 'href="/activity/"')
        self.login(self.vic); self.assertNotContains(self.client.get("/"), 'href="/activity/"')

    def test_each_account_sees_only_its_own_history(self):
        self.assertTrue(all("BETA" not in s for s in self.summaries(user=self.alice)))
        self.assertEqual(self.summaries(user=self.bella), ["Created property “BETA Plaza”"])


class ContentTests(FeedBase):
    def test_newest_first_with_who_what_and_changes(self):
        self.login(self.alice)
        r = self.client.get("/activity/")
        rows = list(r.context["object_list"])
        self.assertEqual([e.summary for e in rows], ["Created contact “Zed Roofer”", "Updated property “Birch Lodge” (City)", "Created property “Birch Lodge”"])
        for text in ("alice@acme.test", "carol@both.test", "City: Ogdenville → Springfield"):
            self.assertContains(r, text)

    def test_filters(self):
        self.login(self.alice)
        self.assertEqual(self.summaries("?action=updated"), ["Updated property “Birch Lodge” (City)"])
        self.assertEqual(sorted(self.summaries("?type=property")), ["Created property “Birch Lodge”", "Updated property “Birch Lodge” (City)"])
        self.assertEqual(self.summaries(f"?who={self.carol.pk}"), ["Created contact “Zed Roofer”"])
        self.assertEqual(self.summaries("?q=roofer"), ["Created contact “Zed Roofer”"])
        self.assertEqual(len(self.summaries(f"?who={self.bella.pk}")), 3, "a person from another account is ignored, not an error")
        self.assertEqual(len(self.summaries("?action=nonsense&type=junk&who=abc")), 3)

    def test_filtering_by_record(self):
        self.login(self.alice)
        self.assertEqual(len(self.summaries(f"?object=property:{self.birch.pk}")), 2)
        for junk in ("property:abc", "../..", "property:1;drop", "x", "PROPERTY:1", "property:"):
            self.assertEqual(len(self.summaries(f"?object={junk}")), 3, junk)

    def test_pagination_keeps_filters(self):
        with use_account(self.acme):
            for i in range(60):
                Property.objects.create(name=f"Bulk {i}")
        self.login(self.alice)
        r = self.client.get("/activity/?type=property")
        self.assertEqual((len(r.context["object_list"]), r.context["paginator"].count), (50, 62))
        self.assertContains(r, "type=property&page=2")
        self.assertEqual(len(self.client.get("/activity/?type=property&page=2").context["object_list"]), 12)

    def test_user_supplied_text_is_escaped(self):
        self.login(self.alice)
        self.client.post(f"/properties/{self.birch.pk}/", {"name": XSS, "property_type": "single_family", "city": "Springfield"})
        r = self.client.get("/activity/")
        self.assertNotContains(r, XSS); self.assertContains(r, "&lt;script&gt;")


class JumpTests(FeedBase):
    def entry(self, **kw):
        with use_account(self.acme):
            return ActivityLog.objects.filter(**kw).order_by("id").first()

    def test_jump_opens_the_record_if_it_still_exists(self):
        self.login(self.alice)
        e = self.entry(target_type="property", action="created")
        self.assertRedirects(self.client.get(f"/activity/{e.pk}/"), f"/properties/{self.birch.pk}/", fetch_redirect_response=False)
        self.assertContains(self.client.get("/activity/"), f'href="/activity/{e.pk}/"')

    def test_gone_records_and_pageless_entries_explain_instead_of_failing(self):
        self.login(self.alice)
        self.client.post(f"/properties/{self.birch.pk}/archive/")
        e = self.entry(target_type="property", action="created")
        r = self.client.get(f"/activity/{e.pk}/", follow=True)
        self.assertEqual(r.redirect_chain[-1][0], "/activity/"); self.assertContains(r, "That record no longer exists.")
        with use_account(self.acme):
            ActivityLog(account_id=self.acme.pk, action="exported", summary="Exported x", target_type="export").save()
        ex = self.entry(action="exported")
        self.assertContains(self.client.get(f"/activity/{ex.pk}/", follow=True), "That record no longer exists.")

    def test_roles_and_tenants(self):
        e = self.entry(target_type="property")
        self.login(self.vic); self.assertEqual(self.client.get(f"/activity/{e.pk}/").status_code, 403)
        self.login(self.bella); self.assertEqual(self.client.get(f"/activity/{e.pk}/").status_code, 404)
        self.client.logout(); self.assertEqual(self.client.get(f"/activity/{e.pk}/").status_code, 302)


class HistoryPanelTests(FeedBase):
    def test_record_page_shows_its_own_recent_history_with_a_link_to_all_of_it(self):
        self.login(self.alice)
        r = self.client.get(f"/properties/{self.birch.pk}/")
        for text in ("<h2>History", "Created property “Birch Lodge”", "City: Ogdenville → Springfield", "alice@acme.test"):
            self.assertContains(r, text)
        self.assertContains(r, f'href="/activity/?object=property:{self.birch.pk}"')
        self.assertNotContains(r, "Zed Roofer")                                           # other records' entries are not shown

    def test_panel_shows_only_the_latest_entries(self):
        self.login(self.alice)
        with use_account(self.acme):
            for i in range(12):
                p = Property.objects.get(pk=self.birch.pk); p.notes = f"v{i}"; p.save()
        body = self.client.get(f"/properties/{self.birch.pk}/").content.decode()
        self.assertEqual(body.count("Updated property"), 8)

    def test_no_panel_for_viewers_or_on_create_pages(self):
        o = self.occ(self.acme, self.maple, "Pump", D(2026, 11, 15))
        self.login(self.vic); self.assertNotContains(self.client.get(f"/maintenance/{o.pk}/"), "<h2>History")
        self.login(self.alice); self.assertNotContains(self.client.get("/properties/new/"), "<h2>History")
        self.assertContains(self.client.get(f"/maintenance/{o.pk}/"), "<h2>History")
