from datetime import datetime, timezone as dt_timezone
from unittest import mock
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from accounts.models import Account
from accounts.timezones import DEFAULT, load_zone, timezone_choices, validate_timezone

UTC = dt_timezone.utc


def at(*ymdhms):
    """Freeze 'now' at this UTC instant."""
    return mock.patch("django.utils.timezone.now", return_value=datetime(*ymdhms, tzinfo=UTC))


class ZoneHelperTests(SimpleTestCase):
    def test_choices_offer_canonical_names_only(self):
        values = [v for v, _ in timezone_choices()]
        self.assertEqual(values[0], "UTC")
        self.assertEqual(values[1:], sorted(values[1:]))
        self.assertEqual(len(values), len(set(values)))
        for wanted in ("America/New_York", "Asia/Kolkata", "Asia/Kathmandu", "Pacific/Chatham", "Europe/London",
                       "Pacific/Kiritimati", "America/Argentina/Buenos_Aires"):
            self.assertIn(wanted, values)
        for legacy in ("US/Eastern", "EST5EDT", "Etc/GMT+5", "posix/UTC", "GMT", "Zulu"):
            self.assertNotIn(legacy, values)
        self.assertIn(("America/New_York", "America/New York"), timezone_choices())

    def test_validate_accepts_real_zones_and_rejects_everything_else(self):
        for good in ("UTC", "America/Chicago", "Asia/Kolkata", "US/Eastern"):      # loadable legacy names still validate
            validate_timezone(good)
        for bad in ("Mars/Olympus", "", "../../etc/passwd", "America/", None, "A" * 100, "UTC\x00", "  ", 5):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                validate_timezone(bad)

    def test_load_zone_never_raises(self):
        self.assertEqual(load_zone("Europe/London").key, "Europe/London")
        for bad in ("Mars/Olympus", "", None, "../x", "UTC\x00", 5, "A" * 500):
            self.assertEqual(load_zone(bad).key, DEFAULT, repr(bad))


class AccountClockTests(TestCase):
    def acct(self, tz):
        return Account.objects.create(name=f"A-{tz}", timezone=tz)

    def test_default_is_utc_and_bad_names_fail_validation(self):
        self.assertEqual(Account.objects.create(name="x").timezone, "UTC")
        with self.assertRaises(ValidationError):
            Account(name="x", timezone="Mars/Olympus").full_clean()
        Account(name="x", timezone="Asia/Tokyo").full_clean()

    def test_local_midnight_flips_the_date_exactly_on_time(self):
        cases = [  # (zone, last second of the old day in UTC, first second of the new day in UTC)
            ("America/New_York", (2026, 11, 10, 4, 59, 59), (2026, 11, 10, 5, 0, 0)),     # EST
            ("America/New_York", (2026, 7, 15, 3, 59, 59), (2026, 7, 15, 4, 0, 0)),       # EDT
            ("Asia/Kolkata", (2026, 11, 10, 18, 29, 59), (2026, 11, 10, 18, 30, 0)),      # +5:30
            ("Asia/Kathmandu", (2026, 11, 10, 18, 14, 59), (2026, 11, 10, 18, 15, 0)),    # +5:45
            ("Pacific/Chatham", (2026, 11, 10, 10, 14, 59), (2026, 11, 10, 10, 15, 0)),   # +13:45 (DST)
            ("Pacific/Kiritimati", (2026, 11, 9, 9, 59, 59), (2026, 11, 9, 10, 0, 0)),    # +14
            ("Pacific/Pago_Pago", (2026, 11, 10, 10, 59, 59), (2026, 11, 10, 11, 0, 0)),  # -11
        ]
        for tz, before, after in cases:
            acct = self.acct(tz)
            with at(*before):
                old = acct.today()
            with at(*after):
                new = acct.today()
            self.assertEqual((new - old).days, 1, f"{tz} {before}")

    def test_dst_transition_days_do_not_skip_or_repeat_a_date(self):
        ny = self.acct("America/New_York")
        # Spring forward Mar 8 2026 (23-hour day) and fall back Nov 1 2026 (25-hour day).
        for start_utc in ((2026, 3, 8, 5, 0, 0), (2026, 11, 1, 4, 0, 0)):      # local midnight starting that day
            first = datetime(*start_utc, tzinfo=UTC)
            seen = []
            for hours in range(0, 27):
                from datetime import timedelta
                with mock.patch("django.utils.timezone.now", return_value=first + timedelta(hours=hours)):
                    seen.append(ny.today())
            self.assertEqual(sorted(set(seen)), [seen[0], seen[-1]] if seen[0] != seen[-1] else [seen[0]])
            self.assertEqual(seen, sorted(seen), "dates never go backwards")
            self.assertEqual((seen[-1] - seen[0]).days, 1)

    def test_the_same_instant_is_different_dates_around_the_world(self):
        with at(2026, 11, 10, 12, 0, 0):
            kiritimati, pago, utc = self.acct("Pacific/Kiritimati").today(), self.acct("Pacific/Pago_Pago").today(), self.acct("UTC").today()
        self.assertEqual((kiritimati.day, utc.day, pago.day), (11, 10, 10))
        self.assertEqual((kiritimati - pago).days, 1)

    def test_corrupt_stored_value_falls_back_to_utc(self):
        acct = self.acct("UTC")
        Account.objects.filter(pk=acct.pk).update(timezone="Nope/Zone")
        acct.refresh_from_db()
        with at(2026, 11, 10, 23, 30, 0):
            self.assertEqual((acct.zone.key, acct.today().day), ("UTC", 10))
