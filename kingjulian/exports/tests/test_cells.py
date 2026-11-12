import csv
import io
from datetime import date, datetime, time, timezone as dt_tz
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from exports.responses import cell, csv_safe


class FormulaInjectionTests(SimpleTestCase):
    PAYLOADS = ['=1+1', '=HYPERLINK("http://evil.example","click")', "=cmd|' /C calc'!A0", "+SUM(1,2)", "-2+3", "@SUM(A1)",
                "\t=1+1", "\r=1+1", "=IMPORTXML(\"http://evil.example\",\"//a\")", "+cmd|' /C calc'!A0", "-HYPERLINK(\"x\")",
                "@cmd", "+A1", "-A1", "=1", "+1+1", "-1+1", "+1 801-555-0142 ext 5"]

    def test_everything_that_could_run_gets_an_apostrophe(self):
        for p in self.PAYLOADS:
            self.assertEqual(csv_safe(p), "'" + p, repr(p))
            self.assertTrue(cell(p).startswith("'"), repr(p))

    def test_ordinary_text_and_real_phone_numbers_are_untouched(self):
        for ok in ("Maple Duplex", "A=B", "1+1", "x-y", " =leading space", "'quoted", "+1 801-555-0142", "+44 20 7946 0958",
                   "-5", "-5.25", "+1 (801) 555-0142", "-", "", "José", "日本語", "😀", "#hash", "100%"):
            self.assertEqual(csv_safe(ok), ok, repr(ok))

    def test_a_lone_prefix_character_is_neutralised(self):
        for lone in ("=", "+", "@", "\t"):
            self.assertEqual(csv_safe(lone), "'" + lone)


class CellTests(SimpleTestCase):
    def test_numbers_keep_their_sign_and_are_never_sanitised(self):
        self.assertEqual([cell(v) for v in (Decimal("-5.00"), -3, 2.5, Decimal("640.00"), Decimal("1E+3"), 0)],
                         ["-5.00", "-3", "2.5", "640.00", "1000", "0"])

    def test_empty_and_booleans(self):
        self.assertEqual([cell(None), cell(""), cell(True), cell(False)], ["", "", "Yes", "No"])

    def test_dates_and_times_are_iso(self):
        self.assertEqual((cell(date(2026, 11, 5)), cell(time(9, 5)), cell(time(13, 30, 59))), ("2026-11-05", "09:05", "13:30"))

    def test_aware_datetimes_use_the_active_timezone_with_an_offset(self):
        moment = datetime(2026, 11, 10, 18, 47, 12, tzinfo=dt_tz.utc)
        self.assertEqual(cell(moment), "2026-11-10T18:47:12+00:00")
        with timezone.override(ZoneInfo("America/Denver")):
            self.assertEqual(cell(moment), "2026-11-10T11:47:12-07:00")
        with timezone.override(ZoneInfo("Pacific/Kiritimati")):
            self.assertEqual(cell(moment), "2026-11-11T08:47:12+14:00")

    def test_text_round_trips_through_a_real_csv_parser(self):
        nasty = ['plain', 'with, comma', 'with "quotes"', "multi\nline", "crlf\r\nline", "tab\there", "ünïcödé — 日本語 😀", "  padded  ",
                 "trailing,", '"', ",", ""]
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\r\n").writerow([cell(v) for v in nasty])
        back = next(csv.reader(io.StringIO(buf.getvalue(), newline="")))
        self.assertEqual(back, nasty)
