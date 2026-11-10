from datetime import date as D, timedelta
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models import Account
from core.tenancy import use_account
from planner.services import dashboard

from .base import PlannerWorld

T = PlannerWorld.TODAY


def days(n):
    return T + timedelta(days=n)


class DashboardTests(PlannerWorld):
    def dash(self, account=None, limit=6, can_write=True):
        with use_account(account or self.acme):
            return dashboard(T, can_write=can_write, limit=limit)

    # -- tasks ----------------------------------------------------------------------------------
    def test_overdue_oldest_first_with_count_and_remainder(self):
        for i in range(10):
            self.occ(self.acme, self.maple, f"late{i}", days(-(i + 1)))
        d = self.dash(limit=6)
        self.assertEqual((d.overdue_count, len(d.overdue), d.overdue_more), (10, 6, 4))
        self.assertEqual([o.task.title for o in d.overdue], [f"late{i}" for i in (9, 8, 7, 6, 5, 4)])   # most overdue first

    def test_due_windows_have_exact_boundaries(self):
        for offset in (-1, 0, 7, 8, 30, 31):
            self.occ(self.acme, self.maple, f"t{offset}", days(offset))
        d = self.dash()
        self.assertEqual(d.overdue_count, 1)
        self.assertEqual([o.task.title for o in d.due_week], ["t0", "t7"])        # today..today+7 inclusive
        self.assertEqual(d.due_week_count, 2)
        self.assertEqual(d.due_month_count, 4)                                     # t0, t7, t8, t30
        self.assertEqual(d.open_count, 6)

    def test_done_skipped_and_archived_tasks_do_not_count_as_work(self):
        self.occ(self.acme, self.maple, "done", days(-3), status="done")
        self.occ(self.acme, self.maple, "skipped", days(-3), status="skipped")
        self.occ(self.acme, self.maple, "archived", days(-3), archived=True)
        self.occ(self.acme, self.maple, "archived-soon", days(2), archived=True)
        d = self.dash()
        self.assertEqual((d.overdue_count, d.due_week_count, d.open_count), (0, 0, 0))

    # -- warranties / condition -----------------------------------------------------------------
    def test_warranty_windows(self):
        for offset in (-31, -30, -1, 0, 1, 60, 61):
            self.item(self.acme, self.maple, f"w{offset}", warranty_expires_on=days(offset))
        self.item(self.acme, self.maple, "no-warranty")
        d = self.dash(limit=20)
        self.assertEqual([i.name for i in d.warranties_expiring], ["w0", "w1", "w60"])      # today counts as still expiring
        self.assertEqual([i.name for i in d.warranties_expired], ["w-1", "w-30"])           # newest lapse first

    def test_archived_items_are_ignored(self):
        gone = self.item(self.acme, self.maple, "gone", warranty_expires_on=days(5), condition="replace")
        with use_account(self.acme):
            gone.archive()
        d = self.dash()
        self.assertEqual((d.warranties_expiring, d.attention_count), ([], 0))

    def test_needs_attention_puts_replace_before_poor(self):
        self.item(self.acme, self.maple, "b-poor", condition="poor")
        self.item(self.acme, self.oak, "a-poor", condition="poor")
        self.item(self.acme, self.maple, "z-replace", condition="replace")
        self.item(self.acme, self.maple, "fine", condition="good")
        d = self.dash()
        self.assertEqual(d.attention_count, 3)
        self.assertEqual([i.name for i in d.attention], ["z-replace", "b-poor", "a-poor"])   # then property, then name

    # -- events ---------------------------------------------------------------------------------
    def test_upcoming_events_window_and_ongoing_multi_day(self):
        self.event(self.acme, "today", days(0)); self.event(self.acme, "d14", days(14)); self.event(self.acme, "d15", days(15))
        self.event(self.acme, "yesterday", days(-1)); self.event(self.acme, "ongoing", days(-3), ends_on=days(2))
        self.event(self.acme, "ended yesterday", days(-3), ends_on=days(-1))
        d = self.dash()
        self.assertEqual([e.title for e in d.events_upcoming], ["ongoing", "today", "d14"])

    # -- spend ----------------------------------------------------------------------------------
    def test_spend_window_and_what_counts(self):
        self.occ(self.acme, self.maple, "in-window-edge", days(-364), status="done", cost=Decimal("100.10"))
        self.occ(self.acme, self.maple, "out-of-window", days(-365), status="done", cost=Decimal("9999"))
        self.occ(self.acme, self.oak, "recent", days(-2), status="done", cost=Decimal("50.05"))
        self.occ(self.acme, self.oak, "no-cost", days(-2), status="done")
        self.occ(self.acme, self.oak, "open-with-cost", days(-2), cost=Decimal("777"))
        # Belt and braces: even a skipped/open row that somehow carries a completion date AND a cost is not spend.
        self.occ(self.acme, self.oak, "skipped-dated", days(-2), status="skipped", cost=Decimal("555"), completed_on=days(-2))
        self.occ(self.acme, self.oak, "open-dated", days(-3), status="open", cost=Decimal("444"), completed_on=days(-3))
        d = self.dash()
        self.assertEqual((d.spend_total, d.spend_jobs), (Decimal("150.15"), 2))

    def test_spend_by_property_ranks_by_total_and_caps_at_five(self):
        with use_account(self.acme):
            from properties.models import Property
            extra = [Property.objects.create(name=f"P{i}") for i in range(6)]
        for i, p in enumerate(extra):
            self.occ(self.acme, p, f"j{i}", days(-5), status="done", cost=Decimal(10 * (i + 1)))
        self.occ(self.acme, extra[0], "j0b", days(-6), status="done", cost=Decimal("100"))      # P0 -> 110, jobs=2
        rows = self.dash().spend_by_property
        self.assertEqual([(r["task__property__name"], r["total"], r["jobs"]) for r in rows],
                         [("P0", Decimal("110.00"), 2), ("P5", Decimal("60.00"), 1), ("P4", Decimal("50.00"), 1),
                          ("P3", Decimal("40.00"), 1), ("P2", Decimal("30.00"), 1)])

    # -- isolation, empty state, query bounds ----------------------------------------------------
    def test_other_tenants_data_never_leaks_into_totals_or_lists(self):
        self.occ(self.beta, self.lake, "beta-late", days(-9)); self.item(self.beta, self.lake, "beta-w", warranty_expires_on=days(3), condition="replace")
        self.event(self.beta, "beta-ev", days(2)); self.occ(self.beta, self.lake, "beta-paid", days(-4), status="done", cost=Decimal("5000"))
        d = self.dash()
        self.assertEqual((d.overdue_count, d.attention_count, d.spend_total, d.spend_jobs), (0, 0, 0, 0))
        self.assertEqual((d.warranties_expiring, d.events_upcoming, d.spend_by_property), ([], [], []))
        b = self.dash(account=self.beta)
        self.assertEqual((b.overdue_count, b.attention_count, b.spend_total), (1, 1, Decimal("5000.00")))

    def test_brand_new_account_renders_zeros(self):
        empty = Account.objects.create(name="Empty Co")
        d = self.dash(account=empty)
        self.assertEqual((d.overdue_count, d.due_week_count, d.open_count, d.spend_total, d.spend_jobs), (0, 0, 0, 0, 0))
        self.assertEqual({k: v[0] for k, v in d.counts.items()}, {"Properties": 0, "Units": 0, "Inventory items": 0, "Contacts": 0})

    def test_counts_match_the_data(self):
        d = self.dash()
        self.assertEqual({k: v[0] for k, v in d.counts.items()},
                         {"Properties": 2, "Units": 2, "Inventory items": 2, "Contacts": 1})

    def test_query_count_does_not_grow_with_data(self):
        def count():
            with use_account(self.acme), CaptureQueriesContext(connection) as q:
                dashboard(T, can_write=True)
            return len(q)
        small = count()
        for i in range(25):
            self.occ(self.acme, self.maple, f"o{i}", days(-i - 1)); self.occ(self.acme, self.maple, f"s{i}", days(i), item=self.heater)    # heater lives at Maple
            self.occ(self.acme, self.maple, f"d{i}", days(-i - 2), status="done", cost=Decimal("12.50"))
            self.item(self.acme, self.oak, f"w{i}", warranty_expires_on=days(i), condition="poor")
            self.event(self.acme, f"e{i}", days(i % 14))
        self.assertEqual(small, count())
        self.assertLessEqual(small, 20)
