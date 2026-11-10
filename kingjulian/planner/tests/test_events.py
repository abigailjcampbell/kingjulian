from datetime import date as D, time as T, timedelta

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from django.utils import timezone

from core.tenancy import TenantViolation, use_account
from planner.models import Event

from .base import PlannerWorld

TODAY = timezone.localdate()


def days(n):
    return TODAY + timedelta(days=n)


class EventModelTests(PlannerWorld):
    def make(self, **kw):
        with use_account(self.acme):
            return Event.objects.create(title="x", starts_on=D(2026, 11, 10), **{**kw})

    def test_validation_rules(self):
        bad = [dict(ends_on=D(2026, 11, 9)),                                       # ends before it starts
               dict(end_time=T(10, 0)),                                            # end time but no start time
               dict(start_time=T(10, 0), end_time=T(10, 0)),                       # zero length, same day
               dict(start_time=T(10, 0), end_time=T(9, 0)),                        # ends before it starts, same day
               dict(start_time=T(10, 0), end_time=T(9, 0), ends_on=D(2026, 11, 10)),
               dict(ends_on=D(2026, 11, 10) + timedelta(days=367))]               # absurd span
        for kw in bad:
            with self.assertRaises(ValidationError, msg=str(kw)):
                self.make(**kw)

    def test_valid_shapes(self):
        self.make(start_time=T(9, 0), end_time=T(10, 0))
        self.make(ends_on=D(2026, 11, 12))
        self.make(start_time=T(22, 0), end_time=T(6, 0), ends_on=D(2026, 11, 11))   # overnight is fine across two dates
        self.make(ends_on=D(2026, 11, 10) + timedelta(days=366))

    def test_labels(self):
        self.assertEqual(self.make().date_label, "Nov 10, 2026")
        self.assertEqual(self.make(ends_on=D(2026, 11, 12)).date_label, "Nov 10 – Nov 12, 2026")
        self.assertEqual(self.make().time_label, "All day")
        self.assertEqual(self.make(start_time=T(9, 5)).time_label, "9:05 AM")
        self.assertEqual(self.make(start_time=T(13, 0), end_time=T(14, 30)).time_label, "1:00 PM – 2:30 PM")

    def test_links_are_tenant_checked(self):
        with use_account(self.acme):
            with self.assertRaises(TenantViolation):
                Event.objects.create(title="x", starts_on=D(2026, 11, 10), property=self.lake)
            with self.assertRaises(TenantViolation):
                Event.objects.create(title="x", starts_on=D(2026, 11, 10), contact=self.beta_contact)


class EventViewTests(PlannerWorld):
    def setUp(self):
        self.login(self.alice)

    def post(self, **over):
        data = {"title": "City inspection", "kind": "inspection", "starts_on": days(10).isoformat(),
                "property": self.maple.pk, "start_time": "10:00", "end_time": "12:00"}
        data.update(over)
        return self.client.post("/planner/events/new/", data)

    def test_create_edit_archive(self):
        self.assertRedirects(self.post(), "/planner/events/", fetch_redirect_response=False)
        with use_account(self.acme):
            ev = Event.objects.get(title="City inspection")
        self.assertEqual((ev.account_id, str(ev.start_time), ev.property_id), (self.acme.pk, "10:00:00", self.maple.pk))
        self.client.post(f"/planner/events/{ev.pk}/", {"title": "City inspection (moved)", "kind": "inspection",
                                                      "starts_on": days(12).isoformat()})
        with use_account(self.acme):
            moved = Event.objects.get(pk=ev.pk)
        self.assertEqual((moved.title, moved.starts_on, moved.start_time), ("City inspection (moved)", days(12), None))
        self.client.post(f"/planner/events/{ev.pk}/archive/")
        table = self.client.get("/planner/events/?when=", HTTP_HX_REQUEST="true")     # fragment: no flash message
        self.assertNotContains(table, "City inspection (moved)")
        self.assertTrue(Event.all_tenants.get(pk=ev.pk).is_archived)

    def test_bad_input_is_a_form_error_not_a_500(self):
        cases = [(dict(ends_on=days(5).isoformat()), "ends_on"), (dict(end_time="09:00"), "end_time"),
                 (dict(start_time="", end_time="09:00"), "end_time"), (dict(starts_on="nope"), "starts_on"),
                 (dict(title=""), "title"), (dict(start_time="25:99"), "start_time"),
                 (dict(ends_on=(days(10) + timedelta(days=400)).isoformat()), "ends_on")]
        for over, field in cases:
            r = self.post(**over)
            self.assertEqual(r.status_code, 200, over)
            self.assertTrue(r.context["form"].errors.get(field), over)
        self.assertEqual(Event.all_tenants.filter(title="City inspection").count(), 0)

    def test_forged_foreign_property_or_contact_is_rejected(self):
        for over, field in ((dict(property=self.lake.pk), "property"), (dict(contact=self.beta_contact.pk), "contact")):
            r = self.post(**over)
            self.assertTrue(r.context["form"].errors.get(field), over)
        self.assertEqual(Event.all_tenants.filter(title="City inspection").count(), 0)

    def test_calendar_day_link_prefills_the_date_and_junk_is_ignored(self):
        r = self.client.get(f"/planner/events/new/?date={days(3).isoformat()}")
        self.assertEqual(r.context["form"].initial["starts_on"], days(3))
        for junk in ("garbage", "2026-02-30", ""):
            r = self.client.get(f"/planner/events/new/?date={junk}")
            self.assertEqual((r.status_code, "starts_on" in r.context["form"].initial), (200, False), junk)

    def test_list_defaults_to_upcoming_with_past_one_click_away(self):
        self.event(self.acme, "soon", days(3)); self.event(self.acme, "today", days(0)); self.event(self.acme, "gone", days(-9))
        self.event(self.acme, "ongoing", days(-2), ends_on=days(1)); self.event(self.acme, "over", days(-5), ends_on=days(-1))
        titles = lambda q: [e.title for e in self.client.get("/planner/events/" + q).context["object_list"]]
        self.assertEqual(titles(""), ["ongoing", "today", "soon"])
        self.assertEqual(titles("?when=past"), ["over", "gone"])                       # newest first
        self.assertEqual(sorted(titles("?when=")), ["gone", "ongoing", "over", "soon", "today"])

    def test_search_and_type_filter(self):
        self.event(self.acme, "Showing Oak", days(2), kind="showing", property=self.oak)
        self.event(self.acme, "Inspect Maple", days(2), kind="inspection", property=self.maple, contact=self.plumber)
        titles = lambda q: [e.title for e in self.client.get("/planner/events/?" + q).context["object_list"]]
        self.assertEqual(titles("kind=showing"), ["Showing Oak"])
        self.assertEqual(titles("q=dana"), ["Inspect Maple"])             # matches the linked contact
        self.assertEqual(titles("q=oak"), ["Showing Oak"])
        self.assertEqual(titles(f"property={self.lake.pk}"), ["Showing Oak", "Inspect Maple"])   # foreign id ignored, not an error

    def test_other_tenants_events_are_invisible_and_404(self):
        theirs = self.event(self.beta, "BETA secret", days(2))
        self.assertNotContains(self.client.get("/planner/events/?when="), "BETA secret")
        for method, url in (("get", f"/planner/events/{theirs.pk}/"), ("post", f"/planner/events/{theirs.pk}/"),
                            ("post", f"/planner/events/{theirs.pk}/archive/")):
            self.assertEqual(getattr(self.client, method)(url, {"title": "x"}).status_code, 404, url)

    def test_viewer_can_list_but_not_write_or_open_edit_pages(self):
        ev = self.event(self.acme, "visible", days(2))
        self.login(self.vic)
        r = self.client.get("/planner/events/")
        self.assertContains(r, "visible"); self.assertNotContains(r, f'href="/planner/events/{ev.pk}/"')
        self.assertNotContains(r, "+ New")
        for method, url in (("get", "/planner/events/new/"), ("post", "/planner/events/new/"),
                            ("get", f"/planner/events/{ev.pk}/"), ("post", f"/planner/events/{ev.pk}/archive/")):
            self.assertEqual(getattr(self.client, method)(url, {"title": "x", "starts_on": days(1).isoformat()}).status_code, 403, url)
        self.assertEqual(Event.all_tenants.filter(title="x").count(), 0)
