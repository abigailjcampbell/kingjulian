from datetime import date as D, time as T

from django.test import SimpleTestCase

from planner.services import (Entry, agenda, grid_weeks, month_grid, month_label_key, parse_date, parse_days,
                              parse_month, shift_month)


def e(title, start, end=None, **kw):
    return Entry("event", f"event-{title}", title, start, end or start, **kw)


class GridShapeTests(SimpleTestCase):
    def test_week_counts_and_sunday_start(self):
        shapes = {(2026, 2): 4, (2026, 8): 6, (2026, 11): 5, (2028, 2): 5, (2027, 1): 6, (2024, 9): 5}
        for (y, m), rows in shapes.items():
            weeks = grid_weeks(y, m)
            self.assertEqual(len(weeks), rows, (y, m))
            self.assertTrue(all(len(w) == 7 for w in weeks))
            self.assertEqual(weeks[0][0].weekday(), 6, "weeks start on Sunday")
            self.assertEqual(sum(d.month == m for w in weeks for d in w), [31,28,31,30,31,30,31,31,30,31,30,31][m-1] + (y % 4 == 0 and m == 2))

    def test_grid_days_are_contiguous(self):
        flat = [d for w in grid_weeks(2026, 12) for d in w]
        self.assertTrue(all((b - a).days == 1 for a, b in zip(flat, flat[1:])))

    def test_flags(self):
        g = month_grid(2026, 11, [], D(2026, 11, 15))
        cells = [c for w in g for c in w]
        self.assertEqual([c["date"] for c in cells if c["is_today"]], [D(2026, 11, 15)])
        self.assertEqual(sum(c["in_month"] for c in cells), 30)
        self.assertFalse([c for c in cells if c["date"] == D(2026, 12, 3)][0]["in_month"])


class NavigationTests(SimpleTestCase):
    def test_shift_month_across_year_boundaries(self):
        self.assertEqual(shift_month(2026, 12, 1), (2027, 1))
        self.assertEqual(shift_month(2026, 1, -1), (2025, 12))
        self.assertEqual(shift_month(2026, 11, 25), (2028, 12))
        self.assertEqual(shift_month(2026, 3, 0), (2026, 3))
        self.assertEqual(month_label_key(2026, 3), "2026-03")

    def test_parse_month_falls_back_on_anything_odd(self):
        today = D(2026, 3, 9)
        for good, want in (("2026-11", (2026, 11)), ("1900-01", (1900, 1)), ("2200-12", (2200, 12))):
            self.assertEqual(parse_month(good, today), want)
        for bad in ("2026-13", "2026-00", "abc", "", None, "0001-05", "9999-01", "2026-1", "2026-11\n", "2026-11 ",
                    "\n2026-11", "2026-11x", "2026/11", "26-11", "2026-１１"):
            self.assertEqual(parse_month(bad, today), (2026, 3), repr(bad))

    def test_parse_days_and_date(self):
        self.assertEqual([parse_days(x) for x in ("7", "90", "30", "8", "x", None, "-7", "0")], [7, 90, 30, 30, 30, 30, 30, 30])
        today = D(2026, 3, 9)
        self.assertEqual(parse_date("2026-11-02", today), D(2026, 11, 2))
        for bad in ("2026-02-30", "x", "", None, "2026-13-01", "２０２６-１１-０２"):
            self.assertEqual(parse_date(bad, today), today)


class PlacementTests(SimpleTestCase):
    def days_with(self, grid, title):
        return [c["date"] for w in grid for c in w if any(x.title == title for x in c["entries"])]

    def test_multi_day_entry_appears_on_every_day_it_covers(self):
        roof = e("roof", D(2026, 11, 30), D(2026, 12, 2))
        self.assertEqual(self.days_with(month_grid(2026, 12, [roof], D(2026, 12, 15)),
                                        "roof"), [D(2026, 11, 30), D(2026, 12, 1), D(2026, 12, 2)])
        # the same entry also shows in November's trailing days
        self.assertEqual(len(self.days_with(month_grid(2026, 11, [roof], D(2026, 11, 15)), "roof")), 3)

    def test_entry_outside_the_visible_grid_is_ignored(self):
        self.assertEqual(self.days_with(month_grid(2026, 11, [e("far", D(2027, 5, 5))], D(2026, 11, 1)), "far"), [])

    def test_very_long_event_does_not_explode(self):
        year_long = e("year", D(2026, 1, 1), D(2026, 12, 31))
        g = month_grid(2026, 6, [year_long], D(2026, 6, 1))
        self.assertTrue(all(c["total"] == 1 for w in g for c in w))

    def test_overflow_is_counted_not_dropped(self):
        many = [e(f"t{i}", D(2026, 11, 10)) for i in range(5)]
        cell = [c for w in month_grid(2026, 11, many, D(2026, 11, 1)) for c in w if c["date"] == D(2026, 11, 10)][0]
        self.assertEqual((len(cell["entries"]), cell["more"], cell["total"]), (3, 2, 5))

    def test_exactly_at_the_cap_has_no_overflow(self):
        three = [e(f"t{i}", D(2026, 11, 10)) for i in range(3)]
        cell = [c for w in month_grid(2026, 11, three, D(2026, 11, 1)) for c in w if c["date"] == D(2026, 11, 10)][0]
        self.assertEqual((len(cell["entries"]), cell["more"]), (3, 0))
