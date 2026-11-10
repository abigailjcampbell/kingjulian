import time as clock
from datetime import date as D

from django.test import SimpleTestCase

from maintenance.recurrence import AFTER_COMPLETION, FIXED, describe, next_due, normalize_rule


def nxt(rule, mode=FIXED, anchor=D(2026, 1, 1), scheduled=None, done=None):
    scheduled = scheduled or anchor
    return next_due(rule, mode=mode, anchor=anchor, scheduled=scheduled, completed_on=done or scheduled)


class FixedScheduleTests(SimpleTestCase):
    def test_month_end_anchor_never_drifts(self):
        a = D(2026, 1, 31)
        self.assertEqual(nxt("FREQ=MONTHLY", anchor=a), D(2026, 2, 28))
        self.assertEqual(nxt("FREQ=MONTHLY", anchor=a, scheduled=D(2026, 2, 28)), D(2026, 3, 31))
        self.assertEqual(nxt("FREQ=MONTHLY", anchor=a, scheduled=D(2026, 3, 31)), D(2026, 4, 30))

    def test_late_completion_skips_missed_dates(self):
        self.assertEqual(nxt("FREQ=MONTHLY", scheduled=D(2026, 1, 1), done=D(2026, 3, 20)), D(2026, 4, 1))

    def test_early_completion_keeps_the_schedule(self):
        self.assertEqual(nxt("FREQ=MONTHLY", scheduled=D(2026, 3, 1), done=D(2026, 2, 20)), D(2026, 4, 1))

    def test_intervals(self):
        self.assertEqual(nxt("FREQ=WEEKLY;INTERVAL=2", scheduled=D(2026, 1, 1)), D(2026, 1, 15))
        self.assertEqual(nxt("FREQ=MONTHLY;INTERVAL=6", scheduled=D(2026, 1, 1)), D(2026, 7, 1))

    def test_leap_day(self):
        a = D(2028, 2, 29)
        self.assertEqual(nxt("FREQ=YEARLY", anchor=a, scheduled=a), D(2029, 2, 28))
        self.assertEqual(nxt("FREQ=YEARLY", anchor=a, scheduled=D(2031, 2, 28)), D(2032, 2, 29))

    def test_long_catch_up_is_correct_and_fast(self):
        t = clock.time()
        got = nxt("FREQ=DAILY", anchor=D(2020, 1, 1), done=D(2026, 11, 6))
        self.assertEqual(got, D(2026, 11, 7))
        self.assertLess(clock.time() - t, 0.5)

    def test_complex_rules_use_dateutil(self):
        self.assertEqual(nxt("FREQ=WEEKLY;BYDAY=MO,TH", scheduled=D(2026, 1, 1)), D(2026, 1, 5))   # Thu -> Mon

    def test_count_rules_exhaust(self):
        self.assertEqual(nxt("FREQ=MONTHLY;COUNT=3", scheduled=D(2026, 2, 1)), D(2026, 3, 1))
        self.assertIsNone(nxt("FREQ=MONTHLY;COUNT=3", scheduled=D(2026, 3, 1)))

    def test_one_off_has_no_next(self):
        self.assertIsNone(nxt(""))


class AfterCompletionTests(SimpleTestCase):
    def test_counts_from_the_day_it_was_done(self):
        self.assertEqual(nxt("FREQ=MONTHLY;INTERVAL=3", AFTER_COMPLETION, done=D(2026, 5, 10)), D(2026, 8, 10))

    def test_clamps_month_end(self):
        self.assertEqual(nxt("FREQ=MONTHLY", AFTER_COMPLETION, done=D(2026, 1, 31)), D(2026, 2, 28))
        self.assertEqual(nxt("FREQ=MONTHLY;INTERVAL=3", AFTER_COMPLETION, done=D(2026, 11, 30)), D(2027, 2, 28))

    def test_due_date_is_irrelevant(self):
        self.assertEqual(nxt("FREQ=WEEKLY", AFTER_COMPLETION, scheduled=D(2026, 1, 1), done=D(2026, 6, 3)),
                         D(2026, 6, 10))


class ValidationTests(SimpleTestCase):
    def test_normalises(self):
        self.assertEqual(normalize_rule("rrule:freq=yearly"), "FREQ=YEARLY")
        self.assertEqual(normalize_rule("  "), "")

    def test_rejects_bad_rules(self):
        for bad in ["FREQ=HOURLY", "FREQ=MONTHLY;BYHOUR=9", "DTSTART:20260101\nFREQ=DAILY", "banana",
                    "FREQ=MONTHLY;INTERVAL=0", "FREQ=DAILY;INTERVAL=x", "FREQ=MONTHLY;COUNT=1"]:
            with self.assertRaises(ValueError, msg=bad):
                normalize_rule(bad)

    def test_rejects_impossible_dates_quickly(self):
        t = clock.time()
        with self.assertRaises(ValueError):
            normalize_rule("FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=30")
        self.assertLess(clock.time() - t, 2)

    def test_describe(self):
        self.assertEqual(describe(""), "One-off")
        self.assertEqual(describe("FREQ=MONTHLY;INTERVAL=3"), "Every 3 months")
        self.assertEqual(describe("FREQ=MONTHLY", AFTER_COMPLETION), "Monthly, after completion")
        self.assertTrue(describe("FREQ=WEEKLY;BYDAY=MO").startswith("Custom"))
