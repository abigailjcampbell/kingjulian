from datetime import date as D, timedelta
from decimal import Decimal

import icalendar
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import Account, Membership, User
from core.tenancy import use_account
from core.tests.base import PW
from planner.models import Event

from .base import PlannerWorld

TODAY = timezone.localdate()
XSS = '<script>alert("x")</script>'


def days(n):
    return TODAY + timedelta(days=n)


def cell(response, day):
    return next(c for week in response.context["grid"] for c in week if c["date"] == day)


class DashboardPageTests(PlannerWorld):
    def setUp(self):
        self.login(self.alice)

    def seed(self):
        self.occ(self.acme, self.maple, "Replace smoke detector batteries", days(-10))
        self.occ(self.acme, self.oak, "Test sump pumps", days(3), item=None)
        self.item(self.acme, self.maple, "Samsung Dishwasher", warranty_expires_on=days(25))
        self.item(self.acme, self.oak, "Old Boiler", condition="replace")
        self.event(self.acme, "City inspection", days(5), property=self.maple)
        self.occ(self.acme, self.maple, "Boiler service", days(-40), status="done", cost=Decimal("640"), completed_on=days(-40))

    def test_home_is_the_dashboard_and_shows_the_numbers(self):
        self.seed()
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertTemplateUsed(r, "planner/dashboard.html")
        for text in ("Replace smoke detector batteries", "10 days overdue", "Test sump pumps", "Samsung Dishwasher",
                     "Old Boiler", "City inspection", "$640.00", "1 job", "Maple Duplex"):
            self.assertContains(r, text)
        d = r.context["d"]
        self.assertEqual((d.overdue_count, d.due_week_count, d.attention_count), (1, 1, 1))

    def test_owner_gets_links_to_every_target(self):
        self.seed()
        body = self.client.get("/").content.decode()
        self.assertIn('href="/inventory/', body); self.assertIn('href="/planner/events/', body)
        self.assertIn('href="/maintenance/', body)

    def test_viewer_only_gets_links_to_pages_they_can_open(self):
        self.seed()
        self.login(self.vic)
        body = self.client.get("/").content.decode()
        self.assertIn("Samsung Dishwasher", body); self.assertIn("City inspection", body)
        self.assertNotIn('href="/inventory/', body.replace('href="/inventory/"', ""))      # list page ok, item pages not
        self.assertNotIn('href="/planner/events/', body)
        self.assertRegex(body, r'href="/maintenance/\d+/"')                                # occurrence pages are viewable

    def test_same_named_items_show_which_unit_they_are_in(self):
        with use_account(self.acme):
            from inventory.models import InventoryItem
            for unit in (self.maple_a, self.maple_b):
                InventoryItem.objects.create(property=self.maple, unit=unit, name="Twin Fridge", warranty_expires_on=days(10), condition="poor")
        body = self.client.get("/").content.decode()
        self.assertEqual(body.count("Maple Duplex · Unit A"), 2)          # once under warranties, once under needs-attention
        self.assertEqual(body.count("Maple Duplex · Unit B"), 2)

    def test_anonymous_is_sent_to_login(self):
        self.client.logout()
        r = self.client.get("/")
        self.assertEqual(r.status_code, 302); self.assertIn("/accounts/login/", r["Location"])

    def test_brand_new_account_renders_an_empty_dashboard(self):
        self.client.logout()
        self.client.post("/accounts/signup/", {"account_name": "Fresh LLC", "email": "new@fresh.test", "password1": PW, "password2": PW})
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        for text in ("Nothing overdue.", "No events in the next 14 days.", "None expiring.",
                     "Everything is in good shape.", "No completed work with a cost yet.", "$0.00"):
            self.assertContains(r, text)
        self.assertNotContains(r, "Maple Duplex")

    def test_only_this_accounts_numbers_are_shown(self):
        self.occ(self.beta, self.lake, "BETA overdue", days(-5)); self.event(self.beta, "BETA event", days(1))
        self.occ(self.beta, self.lake, "BETA paid", days(-5), status="done", cost=Decimal("9999"), completed_on=days(-5))
        r = self.client.get("/")
        self.assertNotContains(r, "BETA"); self.assertNotContains(r, "9,999")
        self.assertEqual(r.context["d"].overdue_count, 0)

    def test_user_supplied_text_is_escaped(self):
        self.event(self.acme, XSS, days(2)); self.item(self.acme, self.maple, XSS, warranty_expires_on=days(3), condition="replace")
        self.occ(self.acme, self.maple, XSS, days(-1))
        r = self.client.get("/")
        self.assertNotContains(r, XSS); self.assertContains(r, "&lt;script&gt;")

    def test_page_query_count_is_flat(self):
        def count():
            with CaptureQueriesContext(connection) as q:
                self.assertEqual(self.client.get("/").status_code, 200)
            return len(q)
        self.occ(self.acme, self.maple, "one", days(-1)); small = count()
        for i in range(25):
            self.occ(self.acme, self.maple, f"o{i}", days(-i - 2)); self.event(self.acme, f"e{i}", days(i % 10))
            self.item(self.acme, self.oak, f"w{i}", warranty_expires_on=days(i), condition="poor")
        self.assertEqual(small, count())


class CalendarPageTests(PlannerWorld):
    def setUp(self):
        self.login(self.alice)

    def test_defaults_to_the_current_month(self):
        r = self.client.get("/planner/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["title"], TODAY.strftime("%B %Y"))
        self.assertTrue(r.context["is_current_month"])
        self.assertNotContains(r, ">Today</a>")
        self.assertTrue(cell(r, TODAY)["is_today"])

    def test_navigation_links_cross_year_boundaries(self):
        r = self.client.get("/planner/?month=2026-12")
        self.assertContains(r, 'href="?month=2027-01"'); self.assertContains(r, 'href="?month=2026-11"')
        self.assertContains(r, ">Today</a>")
        r = self.client.get("/planner/?month=2026-01")
        self.assertContains(r, 'href="?month=2025-12"')

    def test_garbage_month_falls_back_to_the_current_one(self):
        for bad in ("2026-13", "x", "9999-01", "2026-1", ""):
            r = self.client.get("/planner/", {"month": bad})
            self.assertEqual((r.status_code, r.context["title"]), (200, TODAY.strftime("%B %Y")), bad)

    def test_all_three_kinds_land_on_the_right_days(self):
        self.event(self.acme, "City inspection", D(2026, 12, 10))
        self.occ(self.acme, self.maple, "Flush heater", D(2026, 12, 10))
        self.item(self.acme, self.maple, "Dishwasher", warranty_expires_on=D(2026, 12, 24))
        self.event(self.acme, "Roof work", D(2026, 12, 30), ends_on=D(2027, 1, 2))
        r = self.client.get("/planner/?month=2026-12")
        self.assertEqual(sorted(e.kind for e in cell(r, D(2026, 12, 10))["entries"]), ["event", "task"])
        self.assertEqual([e.title for e in cell(r, D(2026, 12, 24))["entries"]], ["Warranty ends: Dishwasher"])
        for day in (D(2026, 12, 30), D(2026, 12, 31), D(2027, 1, 1), D(2027, 1, 2)):      # trailing cells show it too
            self.assertEqual([e.title for e in cell(r, day)["entries"]], ["Roof work"], day)
        january = self.client.get("/planner/?month=2027-01")
        self.assertEqual(len([c for w in january.context["grid"] for c in w if any(e.title == "Roof work" for e in c["entries"])]), 4)
        self.assertContains(r, "Flush heater"); self.assertContains(r, "Warranty ends: Dishwasher")

    def test_overdue_and_done_tasks_are_styled_differently(self):
        self.occ(self.acme, self.maple, "late one", days(-2)); self.occ(self.acme, self.maple, "finished", days(-1), status="done")
        body = self.client.get("/planner/").content.decode()
        self.assertRegex(body, r'class="chip task overdue"[^>]*>late one'); self.assertRegex(body, r'class="chip task done"[^>]*>finished')

    def test_crowded_day_collapses_into_a_link_to_the_agenda(self):
        for i in range(5):
            self.event(self.acme, f"crowd{i}", days(1))
        r = self.client.get("/planner/")
        c = cell(r, days(1))
        self.assertEqual((len(c["entries"]), c["more"]), (3, 2))
        self.assertContains(r, f'href="/planner/agenda/?from={days(1).isoformat()}&amp;days=7">+2 more</a>')

    def test_writers_get_add_buttons_viewers_do_not(self):
        self.assertContains(self.client.get("/planner/"), f'/planner/events/new/?date={TODAY.isoformat()}')
        self.assertContains(self.client.get("/planner/"), "+ Event")
        self.login(self.vic)
        r = self.client.get("/planner/")
        self.assertNotContains(r, "/planner/events/new/"); self.assertNotContains(r, "+ Event")

    def test_viewer_sees_chips_but_only_linkable_ones_are_links(self):
        ev = self.event(self.acme, "inspection", days(1)); occ = self.occ(self.acme, self.maple, "pump", days(1))
        self.item(self.acme, self.maple, "boiler", warranty_expires_on=days(1))
        self.login(self.vic)
        body = self.client.get("/planner/").content.decode()
        for t in ("inspection", "pump", "boiler"):
            self.assertIn(t, body)
        self.assertNotIn(f'href="/planner/events/{ev.pk}/"', body); self.assertNotIn('href="/inventory/', body.replace('href="/inventory/"', ""))
        self.assertIn(f'href="/maintenance/{occ.pk}/"', body)

    def test_titles_are_escaped_in_chips_and_tooltips(self):
        self.event(self.acme, XSS, days(1), property=self.maple)
        r = self.client.get("/planner/")
        self.assertNotContains(r, XSS); self.assertContains(r, "&lt;script&gt;")

    def test_other_tenants_entries_never_appear(self):
        self.event(self.beta, "BETA event", days(1)); self.occ(self.beta, self.lake, "BETA task", days(1))
        self.item(self.beta, self.lake, "BETA item", warranty_expires_on=days(1))
        self.assertNotContains(self.client.get("/planner/"), "BETA")
        self.client.logout(); self.login(self.bella)
        body = self.client.get("/planner/").content.decode()
        self.assertIn("BETA event", body); self.assertIn("BETA task", body)

    def test_flat_query_count_and_anonymous_redirect(self):
        def count():
            with CaptureQueriesContext(connection) as q:
                self.assertEqual(self.client.get("/planner/").status_code, 200)
            return len(q)
        self.event(self.acme, "one", days(1)); small = count()
        for i in range(30):
            self.event(self.acme, f"e{i}", days(i % 20)); self.occ(self.acme, self.maple, f"t{i}", days(i % 20))
        self.assertEqual(small, count())
        self.client.logout()
        self.assertEqual(self.client.get("/planner/").status_code, 302)


class AgendaPageTests(PlannerWorld):
    def setUp(self):
        self.login(self.alice)

    def test_groups_by_day_with_overdue_section_on_top(self):
        self.occ(self.acme, self.maple, "old job", days(-8)); self.occ(self.acme, self.maple, "pump", days(2))
        self.event(self.acme, "inspection", days(2)); self.event(self.acme, "later", days(20))
        r = self.client.get("/planner/agenda/")
        body = r.content.decode()
        self.assertEqual([d for d, _ in r.context["groups"]], [days(2), days(20)])
        self.assertLess(body.index("Overdue (1)"), body.index("pump"))
        self.assertContains(r, "8 days overdue")

    def test_window_size_and_bad_params(self):
        self.event(self.acme, "in7", days(6)); self.event(self.acme, "in8", days(7)); self.event(self.acme, "in60", days(59))
        titles = lambda q: [e.title for _, es in self.client.get("/planner/agenda/" + q).context["groups"] for e in es]
        self.assertEqual(titles("?days=7"), ["in7"])
        self.assertEqual(titles("?days=14"), ["in7", "in8"])
        self.assertEqual(titles("?days=60"), ["in7", "in8", "in60"])
        for bad in ("?days=abc", "?days=8", "?days=-5", "?from=garbage", "?from=2026-02-30"):
            self.assertEqual(self.client.get("/planner/agenda/" + bad).status_code, 200, bad)
        self.assertEqual(titles("?days=8"), ["in7", "in8"])         # invalid -> default 30 days

    def test_overdue_section_only_when_the_window_reaches_today(self):
        self.occ(self.acme, self.maple, "old job", days(-8))
        self.assertContains(self.client.get(f"/planner/agenda/?from={days(-3).isoformat()}"), "Overdue (1)")
        self.assertNotContains(self.client.get(f"/planner/agenda/?from={days(1).isoformat()}"), "Overdue")

    def test_multi_day_event_already_underway_shows_on_the_first_visible_day(self):
        self.event(self.acme, "Roof work", days(-2), ends_on=days(3))
        r = self.client.get(f"/planner/agenda/?from={TODAY.isoformat()}&days=7")
        self.assertEqual([(d, [e.title for e in es]) for d, es in r.context["groups"]], [(TODAY, ["Roof work"])])
        self.assertContains(r, "through ")

    def test_empty_window_and_navigation(self):
        r = self.client.get(f"/planner/agenda/?from={days(400).isoformat()}&days=7")
        self.assertContains(r, "Nothing scheduled in this window.")
        self.assertContains(r, f"from={days(393).isoformat()}&amp;days=7"); self.assertContains(r, f"from={days(407).isoformat()}&amp;days=7")

    def test_viewer_gets_no_links_to_forbidden_pages_and_escaping_holds(self):
        ev = self.event(self.acme, XSS, days(1))
        self.login(self.vic)
        r = self.client.get("/planner/agenda/")
        self.assertNotContains(r, f'href="/planner/events/{ev.pk}/"'); self.assertNotContains(r, XSS)
        self.assertContains(r, "&lt;script&gt;")

    def test_other_tenants_entries_never_appear(self):
        self.event(self.beta, "BETA event", days(1)); self.occ(self.beta, self.lake, "BETA late", days(-4))
        self.assertNotContains(self.client.get("/planner/agenda/"), "BETA")


class IcsPageTests(PlannerWorld):
    def setUp(self):
        self.login(self.alice)

    def fetch(self):
        r = self.client.get("/planner/calendar.ics")
        self.assertEqual(r.status_code, 200)
        return r, icalendar.Calendar.from_ical(r.content)

    def test_headers_and_content_type(self):
        r, cal = self.fetch()
        self.assertEqual(r["Content-Type"], "text/calendar; charset=utf-8")
        self.assertIn('attachment; filename="property-calendar.ics"', r["Content-Disposition"])
        self.assertIn("no-store", r["Cache-Control"])
        self.assertEqual(str(cal["X-WR-CALNAME"]), "Acme Rentals")

    def test_contains_all_three_kinds_for_this_account_only(self):
        self.event(self.acme, "inspection", days(3)); self.occ(self.acme, self.maple, "pump", days(4))
        self.item(self.acme, self.maple, "boiler", warranty_expires_on=days(5))
        self.event(self.beta, "BETA event", days(3)); self.occ(self.beta, self.lake, "BETA task", days(4))
        _, cal = self.fetch()
        summaries = {str(v["SUMMARY"]) for v in cal.walk("VEVENT")}
        self.assertEqual(summaries, {"inspection", "pump", "Warranty ends: boiler"})

    def test_download_window_boundaries(self):
        for offset in (-32, -31, 366, 367):
            self.event(self.acme, f"e{offset}", days(offset))
        _, cal = self.fetch()
        self.assertEqual({str(v["SUMMARY"]) for v in cal.walk("VEVENT")}, {"e-31", "e366"})

    def test_uids_are_stable_between_downloads(self):
        self.event(self.acme, "a", days(3)); self.occ(self.acme, self.maple, "b", days(4))
        uids = lambda: sorted(str(v["UID"]) for v in self.fetch()[1].walk("VEVENT"))
        self.assertEqual(uids(), uids()); self.assertEqual(len(set(uids())), 2)

    def test_viewers_may_download_anonymous_may_not_and_only_get_is_allowed(self):
        self.login(self.vic)
        self.assertEqual(self.client.get("/planner/calendar.ics").status_code, 200)
        self.assertEqual(self.client.post("/planner/calendar.ics").status_code, 405)
        self.client.logout()
        r = self.client.get("/planner/calendar.ics")
        self.assertEqual(r.status_code, 302); self.assertIn("/accounts/login/", r["Location"])

    def test_empty_account_still_yields_a_valid_calendar(self):
        self.client.logout()
        self.client.post("/accounts/signup/", {"account_name": "Fresh", "email": "n@fresh.test", "password1": PW, "password2": PW})
        _, cal = self.fetch()
        self.assertEqual(cal.walk("VEVENT"), [])
