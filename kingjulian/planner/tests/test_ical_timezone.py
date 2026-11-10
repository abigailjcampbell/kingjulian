from datetime import date as D, datetime, time as T, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

import icalendar
from django.test import SimpleTestCase, TestCase

from planner.ical import build_ics
from planner.services import Entry

UTC = dt_timezone.utc
NOW = datetime(2026, 11, 6, 12, 0, 0)


def timed(day, start, end=None, **kw):
    return Entry("event", "event-1", "E", day, day, start, end, **kw)


def vevent(entry, tz):
    text = build_ics([entry], account_name="A", account_id=1, now=NOW, zone=ZoneInfo(tz))
    cal = icalendar.Calendar.from_ical(text)
    return cal, cal.walk("VEVENT")[0]


def utc(day, clock, tz):
    """Independent expectation straight from the standard library."""
    return datetime.combine(day, clock, tzinfo=ZoneInfo(tz)).astimezone(UTC)


class TimedEventConversionTests(SimpleTestCase):
    def test_known_offsets(self):
        cases = [("America/New_York", D(2026, 11, 15), T(10, 0), datetime(2026, 11, 15, 15, 0)),    # EST -5
                 ("America/New_York", D(2026, 7, 15), T(10, 0), datetime(2026, 7, 15, 14, 0)),      # EDT -4
                 ("Asia/Kolkata", D(2026, 11, 15), T(10, 0), datetime(2026, 11, 15, 4, 30)),        # +5:30
                 ("Asia/Kathmandu", D(2026, 11, 15), T(10, 0), datetime(2026, 11, 15, 4, 15)),      # +5:45
                 ("Pacific/Auckland", D(2026, 11, 15), T(9, 0), datetime(2026, 11, 14, 20, 0)),     # +13: previous UTC day
                 ("Europe/London", D(2026, 7, 15), T(23, 30), datetime(2026, 7, 15, 22, 30)),       # BST +1
                 ("UTC", D(2026, 11, 15), T(10, 0), datetime(2026, 11, 15, 10, 0))]
        for tz, day, clock, expected in cases:
            _, v = vevent(timed(day, clock, T(23, 0)) if clock < T(23, 0) else timed(day, clock), tz)
            self.assertEqual(v["DTSTART"].dt, expected.replace(tzinfo=UTC), tz)

    def test_end_time_is_converted_with_the_same_zone(self):
        _, v = vevent(timed(D(2026, 11, 15), T(10, 0), T(12, 30)), "America/New_York")
        self.assertEqual((v["DTSTART"].dt, v["DTEND"].dt), (datetime(2026, 11, 15, 15, 0, tzinfo=UTC), datetime(2026, 11, 15, 17, 30, tzinfo=UTC)))

    def test_default_one_hour_length_is_a_real_hour(self):
        _, v = vevent(timed(D(2026, 11, 15), T(23, 30)), "Pacific/Auckland")
        self.assertEqual(v["DTEND"].dt - v["DTSTART"].dt, timedelta(hours=1))

    def test_dst_gap_and_overlap_never_crash_and_stay_ordered(self):
        for day, clock in ((D(2026, 3, 8), T(2, 30)),       # 02:30 does not exist (spring forward)
                           (D(2026, 11, 1), T(1, 30))):     # 01:30 happens twice (fall back)
            _, v = vevent(timed(day, clock, T(3, 30)), "America/New_York")
            self.assertLess(v["DTSTART"].dt, v["DTEND"].dt, day)
            _, v = vevent(timed(day, clock), "America/New_York")
            self.assertEqual(v["DTEND"].dt - v["DTSTART"].dt, timedelta(hours=1), day)

    def test_gap_and_overlap_resolve_the_way_zoneinfo_does(self):
        _, gap = vevent(timed(D(2026, 3, 8), T(2, 30)), "America/New_York")
        _, overlap = vevent(timed(D(2026, 11, 1), T(1, 30)), "America/New_York")
        self.assertEqual(gap["DTSTART"].dt, utc(D(2026, 3, 8), T(2, 30), "America/New_York"))
        self.assertEqual(overlap["DTSTART"].dt, utc(D(2026, 11, 1), T(1, 30), "America/New_York"))

    def test_all_day_items_ignore_the_zone_entirely(self):
        e = Entry("event", "event-1", "Roof", D(2026, 11, 15), D(2026, 11, 16))
        for tz in ("Pacific/Auckland", "Pacific/Pago_Pago", "UTC"):
            _, v = vevent(e, tz)
            self.assertEqual((v["DTSTART"].dt, v["DTEND"].dt), (D(2026, 11, 15), D(2026, 11, 17)), tz)

    def test_calendar_declares_its_zone(self):
        cal, _ = vevent(timed(D(2026, 11, 15), T(10, 0)), "Asia/Kolkata")
        self.assertEqual(str(cal["X-WR-TIMEZONE"]), "Asia/Kolkata")
        text = build_ics([], account_name="A", account_id=1, now=NOW)            # default zone
        self.assertIn("X-WR-TIMEZONE:UTC", text)

    def test_every_timed_stamp_is_utc_marked(self):
        text = build_ics([timed(D(2026, 11, 15), T(10, 0), T(11, 0))], account_name="A", account_id=1, now=NOW,
                         zone=ZoneInfo("America/Chicago"))
        lines = [l for l in text.split("\r\n") if l.startswith(("DTSTART", "DTEND"))]
        self.assertTrue(lines and all(l.endswith("Z") for l in lines), lines)
