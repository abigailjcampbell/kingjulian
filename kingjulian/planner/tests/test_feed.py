from datetime import date as D, time as T

from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.tenancy import TenantContextMissing, use_account
from planner.services import calendar_entries

from .base import PlannerWorld

TODAY = PlannerWorld.TODAY


class FeedTests(PlannerWorld):
    def feed(self, start, end, account=None, can_write=True, today=TODAY):
        with use_account(account or self.acme):
            return calendar_entries(start, end, today=today, can_write=can_write)

    def titles(self, *a, **kw):
        return [e.title for e in self.feed(*a, **kw)]

    def test_unions_all_three_sources_all_day_first_then_timed(self):
        self.event(self.acme, "City inspection", D(2026, 11, 20), start_time=T(10, 0), property=self.maple)
        self.occ(self.acme, self.maple, "Flush heater", D(2026, 11, 20))
        self.item(self.acme, self.maple, "Dishwasher", warranty_expires_on=D(2026, 11, 20))
        self.event(self.acme, "Insurance review", D(2026, 11, 18))
        self.event(self.acme, "Walkthrough", D(2026, 11, 20))                 # all-day event, same date
        self.event(self.acme, "Early call", D(2026, 11, 20), start_time=T(8, 0))
        entries = self.feed(D(2026, 11, 1), D(2026, 11, 30))
        # Calendar convention: a day's all-day items first (events, tasks, warranties), then timed ones by clock.
        self.assertEqual([(e.start, e.kind, e.title) for e in entries], [
            (D(2026, 11, 18), "event", "Insurance review"),
            (D(2026, 11, 20), "event", "Walkthrough"),
            (D(2026, 11, 20), "task", "Flush heater"),
            (D(2026, 11, 20), "warranty", "Warranty ends: Dishwasher"),
            (D(2026, 11, 20), "event", "Early call"),
            (D(2026, 11, 20), "event", "City inspection")])
        self.assertEqual(len({e.uid for e in entries}), 6)

    def test_range_is_inclusive_at_both_ends_for_every_source(self):
        for day, name in ((D(2026, 11, 1), "first"), (D(2026, 11, 30), "last"),
                          (D(2026, 10, 31), "before"), (D(2026, 12, 1), "after")):
            self.event(self.acme, f"ev-{name}", day)
            self.occ(self.acme, self.maple, f"task-{name}", day)
            self.item(self.acme, self.maple, f"item-{name}", warranty_expires_on=day)
        got = set(self.titles(D(2026, 11, 1), D(2026, 11, 30)))
        self.assertEqual(got, {"ev-first", "ev-last", "task-first", "task-last",
                               "Warranty ends: item-first", "Warranty ends: item-last"})

    def test_multi_day_events_overlapping_the_window_are_included(self):
        self.event(self.acme, "spans in from before", D(2026, 10, 28), ends_on=D(2026, 11, 2))
        self.event(self.acme, "spans out after", D(2026, 11, 29), ends_on=D(2026, 12, 3))
        self.event(self.acme, "covers whole window", D(2026, 10, 1), ends_on=D(2026, 12, 31))
        self.event(self.acme, "ended just before", D(2026, 10, 25), ends_on=D(2026, 10, 31))
        self.event(self.acme, "starts just after", D(2026, 12, 1), ends_on=D(2026, 12, 4))
        self.event(self.acme, "single day inside", D(2026, 11, 15))
        got = set(self.titles(D(2026, 11, 1), D(2026, 11, 30)))
        self.assertEqual(got, {"spans in from before", "spans out after", "covers whole window", "single day inside"})

    def test_task_statuses_and_exclusions(self):
        d = D(2026, 11, 10)
        self.occ(self.acme, self.maple, "open-late", D(2026, 11, 10))                       # before TODAY
        self.occ(self.acme, self.maple, "open-soon", D(2026, 11, 20))
        self.occ(self.acme, self.maple, "finished", D(2026, 11, 11), status="done")
        self.occ(self.acme, self.maple, "skipped", D(2026, 11, 12), status="skipped")
        self.occ(self.acme, self.maple, "archived-task", D(2026, 11, 13), archived=True)
        by = {e.title: e.status for e in self.feed(D(2026, 11, 1), D(2026, 11, 30))}
        self.assertEqual(by, {"open-late": "overdue", "open-soon": "open", "finished": "done"})

    def test_task_due_today_is_open_not_overdue(self):
        self.occ(self.acme, self.maple, "due-today", TODAY)
        self.assertEqual(self.feed(TODAY, TODAY)[0].status, "open")

    def test_warranties_and_archived_rows(self):
        self.item(self.acme, self.maple, "ok", warranty_expires_on=D(2026, 11, 20))
        self.item(self.acme, self.maple, "lapsed", warranty_expires_on=D(2026, 11, 2))
        self.item(self.acme, self.maple, "none")
        gone = self.item(self.acme, self.maple, "archived", warranty_expires_on=D(2026, 11, 21))
        ev = self.event(self.acme, "archived event", D(2026, 11, 22))
        with use_account(self.acme):
            gone.archive(); ev.archive()
        by = {e.title: e.status for e in self.feed(D(2026, 11, 1), D(2026, 11, 30))}
        self.assertEqual(by, {"Warranty ends: ok": "expiring", "Warranty ends: lapsed": "expired"})

    def test_tenant_isolation(self):
        self.event(self.acme, "ACME event", D(2026, 11, 12)); self.event(self.beta, "BETA event", D(2026, 11, 12))
        self.occ(self.acme, self.maple, "ACME task", D(2026, 11, 12)); self.occ(self.beta, self.lake, "BETA task", D(2026, 11, 12))
        self.item(self.acme, self.maple, "acme-item", warranty_expires_on=D(2026, 11, 12))
        self.item(self.beta, self.lake, "beta-item", warranty_expires_on=D(2026, 11, 12))
        mine = " | ".join(self.titles(D(2026, 11, 1), D(2026, 11, 30)))
        theirs = " | ".join(self.titles(D(2026, 11, 1), D(2026, 11, 30), account=self.beta))
        self.assertIn("ACME", mine); self.assertNotIn("BETA", mine.upper().replace("ACME", ""))
        self.assertNotIn("beta-item", mine); self.assertNotIn("acme-item", theirs); self.assertNotIn("ACME", theirs)
        self.assertEqual(len(self.feed(D(2026, 11, 1), D(2026, 11, 30), account=self.beta)), 3)

    def test_links_only_where_the_user_can_open_the_page(self):
        self.event(self.acme, "e", D(2026, 11, 12)); self.occ(self.acme, self.maple, "t", D(2026, 11, 12))
        self.item(self.acme, self.maple, "i", warranty_expires_on=D(2026, 11, 12))
        viewer = {e.kind: e.url for e in self.feed(D(2026, 11, 1), D(2026, 11, 30), can_write=False)}
        writer = {e.kind: e.url for e in self.feed(D(2026, 11, 1), D(2026, 11, 30), can_write=True)}
        self.assertEqual((viewer["event"], viewer["warranty"]), (None, None))
        self.assertTrue(viewer["task"].startswith("/maintenance/"))
        self.assertTrue(all(writer.values()))

    def test_identical_items_are_told_apart_by_unit(self):
        with use_account(self.acme):
            from inventory.models import InventoryItem
            InventoryItem.objects.create(property=self.maple, unit=self.maple_a, name="Refrigerator", warranty_expires_on=D(2026, 11, 20))
            InventoryItem.objects.create(property=self.maple, unit=self.maple_b, name="Refrigerator", warranty_expires_on=D(2026, 11, 21))
            InventoryItem.objects.create(property=self.oak, name="Refrigerator", warranty_expires_on=D(2026, 11, 22))
        self.assertEqual([e.where for e in self.feed(D(2026, 11, 1), D(2026, 11, 30))],
                         ["Maple Duplex / Unit A", "Maple Duplex / Unit B", "Oak Cottage"])

    def test_always_exactly_three_queries_however_much_data(self):
        def count():
            with use_account(self.acme), CaptureQueriesContext(connection) as q:
                calendar_entries(D(2026, 11, 1), D(2026, 11, 30), today=TODAY, can_write=True)
            return len(q)
        self.occ(self.acme, self.maple, "t0", D(2026, 11, 5))
        small = count()
        for i in range(30):
            d = D(2026, 11, 1 + i % 28)
            self.event(self.acme, f"e{i}", d, property=self.maple, contact=self.plumber)
            self.occ(self.acme, self.maple, f"t{i+1}", d, item=self.heater)
            self.item(self.acme, self.oak, f"i{i}", warranty_expires_on=d)
        self.assertEqual((small, count()), (3, 3))

    def test_feed_refuses_to_run_without_a_tenant_context(self):
        with self.assertRaises(TenantContextMissing):
            calendar_entries(D(2026, 11, 1), D(2026, 11, 30), today=TODAY, can_write=True)
