from datetime import date as D, datetime, time as T, timezone as tz_

import icalendar
from django.test import SimpleTestCase

from planner.ical import build_ics, escape, fold
from planner.services import Entry

NOW = datetime(2026, 11, 6, 12, 0, 0)
UTC = tz_.utc


def ics(*entries, name="Acme, Inc."):
    text = build_ics(list(entries), account_name=name, account_id=7, now=NOW)
    return text, icalendar.Calendar.from_ical(text)


def ev(title="E", start=D(2026, 11, 15), end=None, **kw):
    return Entry(kw.pop("kind", "event"), kw.pop("uid", "event-1"), title, start, end or start, **kw)


class EscapeAndFoldTests(SimpleTestCase):
    def test_escape(self):
        self.assertEqual(escape("a,b;c\\d"), "a\\,b\\;c\\\\d")
        self.assertEqual(escape("l1\nl2\r\nl3\rl4"), "l1\\nl2\\nl3\\nl4")

    def test_fold_never_exceeds_75_octets_or_splits_characters(self):
        for text in ("SUMMARY:" + "x" * 300, "SUMMARY:" + "Inspección de la caldera — año " * 8, "D:" + "日本語のテキスト" * 20,
                     "D:" + "😀" * 60, "short"):
            physical = fold(text).split("\r\n")
            self.assertTrue(all(len(p.encode()) <= 75 for p in physical), text[:20])
            self.assertTrue(all(p.startswith(" ") for p in physical[1:]))
            self.assertEqual(physical[0] + "".join(p[1:] for p in physical[1:]), text)   # lossless


class DocumentTests(SimpleTestCase):
    def test_parses_as_valid_icalendar(self):
        text, cal = ics(ev("A"), ev("B", uid="event-2"))
        self.assertEqual(cal["VERSION"], "2.0")
        self.assertEqual(len(cal.walk("VEVENT")), 2)
        self.assertEqual(text.count("\r\n"), text.count("\n"), "every line ending is CRLF")
        self.assertTrue(text.endswith("END:VCALENDAR\r\n"))

    def test_all_day_end_is_exclusive(self):
        _, cal = ics(ev(start=D(2026, 11, 30), end=D(2026, 12, 2)))
        v = cal.walk("VEVENT")[0]
        self.assertEqual((v["DTSTART"].dt, v["DTEND"].dt), (D(2026, 11, 30), D(2026, 12, 3)))
        self.assertEqual(v["TRANSP"], "TRANSPARENT")

    def test_timed_events_are_floating_local_times(self):
        _, cal = ics(ev(start_time=T(10, 0), end_time=T(12, 30)))
        v = cal.walk("VEVENT")[0]
        self.assertEqual((v["DTSTART"].dt, v["DTEND"].dt), (datetime(2026, 11, 15, 10), datetime(2026, 11, 15, 12, 30)))
        self.assertIsNone(v["DTSTART"].dt.tzinfo)
        self.assertNotIn("TRANSP", v)

    def test_timed_events_are_written_in_utc(self):
        _, cal = ics(ev(start_time=T(10, 0), end_time=T(12, 30)))          # default zone is UTC
        v = cal.walk("VEVENT")[0]
        self.assertEqual((v["DTSTART"].dt, v["DTEND"].dt), (datetime(2026, 11, 15, 10, tzinfo=UTC), datetime(2026, 11, 15, 12, 30, tzinfo=UTC)))
        self.assertNotIn("TRANSP", v)

    def test_missing_end_time_defaults_to_one_hour_even_across_midnight(self):
        _, cal = ics(ev(start_time=T(23, 30)))
        v = cal.walk("VEVENT")[0]
        self.assertEqual((v["DTSTART"].dt, v["DTEND"].dt), (datetime(2026, 11, 15, 23, 30, tzinfo=UTC), datetime(2026, 11, 16, 0, 30, tzinfo=UTC)))

    def test_multi_day_timed_event_uses_end_date_and_time(self):
        _, cal = ics(ev(start=D(2026, 11, 15), end=D(2026, 11, 17), start_time=T(9, 0), end_time=T(8, 0)))
        v = cal.walk("VEVENT")[0]
        self.assertEqual((v["DTSTART"].dt, v["DTEND"].dt), (datetime(2026, 11, 15, 9, tzinfo=UTC), datetime(2026, 11, 17, 8, tzinfo=UTC)))
    
    def test_special_characters_round_trip(self):
        title = "Inspection, city; \"Maple\" — año 日本"
        _, cal = ics(ev(title, where="Maple Duplex", detail="With: Dana\nBring keys; ID, badge"))
        v = cal.walk("VEVENT")[0]
        self.assertEqual(str(v["SUMMARY"]), title)
        self.assertEqual(str(v["DESCRIPTION"]), "Maple Duplex\nWith: Dana\nBring keys; ID, badge")
        self.assertEqual(str(v["LOCATION"]), "Maple Duplex")

    def test_status_is_visible_in_the_title(self):
        _, cal = ics(ev("Pump", kind="task", uid="task-1", status="overdue"), ev("Filter", kind="task", uid="task-2", status="done"),
                     ev("Fine", kind="task", uid="task-3", status="open"))
        self.assertEqual([str(v["SUMMARY"]) for v in cal.walk("VEVENT")], ["Pump (overdue)", "Filter (done)", "Fine"])

    def test_uids_are_stable_unique_and_scoped_to_the_account(self):
        a, _ = ics(ev(), ev(uid="task-9", kind="task"))
        b, _ = ics(ev(), ev(uid="task-9", kind="task"))
        uids = [l for l in a.split("\r\n") if l.startswith("UID:")]
        self.assertEqual(uids, [l for l in b.split("\r\n") if l.startswith("UID:")])
        self.assertEqual(len(set(uids)), 2)
        other = build_ics([ev()], account_name="x", account_id=8, now=NOW)
        self.assertNotIn(uids[0], other)

    def test_empty_calendar_is_still_valid(self):
        _, cal = ics()
        self.assertEqual(cal.walk("VEVENT"), [])

    def test_account_name_is_escaped(self):
        text, _ = ics(name="Smith; Jones, LLC")
        self.assertIn("X-WR-CALNAME:Smith\\; Jones\\, LLC", text)

