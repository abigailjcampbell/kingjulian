"""Two accounts disagree about today. 'Now' is frozen at 20:00 UTC on today's UTC date: Auckland/Kiritimati are
already tomorrow, Los Angeles is still today."""
from datetime import date as D, datetime, time as T, timedelta, timezone as dt_timezone
from io import StringIO
from unittest import mock
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core import mail
from django.core.management import call_command
from django.utils import timezone

from accounts.models import Account, Membership, User
from core.tenancy import use_account
from core.tests.base import PW
from maintenance.models import MaintenanceTask, Reminder, TaskOccurrence
from maintenance.services import ensure_first_occurrence
from planner.tests.base import PlannerWorld

UTC = dt_timezone.utc
DAY = timezone.now().date()
NOW = datetime.combine(DAY, T(20, 0), tzinfo=UTC)
TOMORROW = DAY + timedelta(days=1)


def at(when=NOW):
    return mock.patch("django.utils.timezone.now", return_value=when)


def today_cell(response):
    return next(c for week in response.context["grid"] for c in week if c["is_today"])


class TZBase(PlannerWorld):
    def setUp(self):
        Account.objects.filter(pk=self.acme.pk).update(timezone="Pacific/Auckland")
        Account.objects.filter(pk=self.beta.pk).update(timezone="America/Los_Angeles")

    def get(self, path, user=None, **kw):
        if user:
            self.login(user)
        with at():
            return self.client.get(path, **kw)

    def post(self, path, data=None, user=None):
        if user:
            self.login(user)
        with at():
            return self.client.post(path, data or {})


class SameInstantTwoDatesTests(TZBase):
    def test_dashboard_uses_the_active_accounts_date(self):
        self.assertEqual(self.get("/", self.alice).context["today"], TOMORROW)      # Auckland
        self.assertEqual(self.get("/", self.bella).context["today"], DAY)           # Los Angeles

    def test_the_clock_belongs_to_the_account_not_the_role(self):
        self.assertEqual(self.get("/", self.vic).context["today"], TOMORROW)

    def test_calendar_highlights_the_local_today(self):
        r = self.get("/planner/", self.alice)
        self.assertEqual(today_cell(r)["date"], TOMORROW)
        self.assertEqual(r.context["title"], TOMORROW.strftime("%B %Y"))
        self.assertEqual(today_cell(self.get("/planner/", self.bella))["date"], DAY)

    def test_countdown_labels_follow_the_local_date(self):
        self.occ(self.acme, self.maple, "Auckland pump", TOMORROW); self.occ(self.beta, self.lake, "LA pump", TOMORROW)
        self.assertContains(self.get("/maintenance/", self.alice), "Due today")
        self.assertContains(self.get("/maintenance/", self.bella), "In 1 day")

    def test_overdue_is_decided_by_the_local_date(self):
        self.occ(self.acme, self.maple, "late in Auckland", DAY); self.occ(self.beta, self.lake, "fine in LA", DAY)
        self.assertEqual(self.get("/", self.alice).context["d"].overdue_count, 1)
        self.assertEqual(self.get("/", self.bella).context["d"].overdue_count, 0)

    def test_completion_form_default_and_future_check_use_the_local_date(self):
        a = self.occ(self.acme, self.maple, "A job", DAY); b = self.occ(self.beta, self.lake, "B job", DAY)
        self.assertEqual(self.get(f"/maintenance/{a.pk}/complete/", self.alice).context["form"].fields["completed_on"].initial, TOMORROW)
        self.assertEqual(self.get(f"/maintenance/{b.pk}/complete/", self.bella).context["form"].fields["completed_on"].initial, DAY)
        # An Auckland owner on their morning of "today" may record today's work; an LA owner may not claim tomorrow.
        ok = self.post(f"/maintenance/{a.pk}/complete/", {"completed_on": TOMORROW.isoformat()}, self.alice)
        self.assertEqual(ok.status_code, 302)
        bad = self.post(f"/maintenance/{b.pk}/complete/", {"completed_on": TOMORROW.isoformat()}, self.bella)
        self.assertEqual(bad.status_code, 200); self.assertTrue(bad.context["form"].errors.get("completed_on"))
        with use_account(self.acme):
            self.assertEqual(TaskOccurrence.objects.get(pk=a.pk).status, "done")
        with use_account(self.beta):
            self.assertEqual(TaskOccurrence.objects.get(pk=b.pk).status, "open")

    def test_agenda_starts_at_the_local_today_and_overdue_differs(self):
        self.occ(self.acme, self.maple, "task", DAY); self.occ(self.beta, self.lake, "task", DAY)
        ra, rb = self.get("/planner/agenda/", self.alice), self.get("/planner/agenda/", self.bella)
        self.assertEqual((ra.context["start"], rb.context["start"]), (TOMORROW, DAY))
        self.assertEqual((ra.context["overdue_count"], rb.context["overdue_count"]), (1, 0))

    def test_ics_window_is_anchored_to_the_local_today(self):
        offsets = (-32, -31, -30, 366, 367, 368)
        for off in offsets:
            self.event(self.acme, f"e{off}", DAY + timedelta(days=off)); self.event(self.beta, f"e{off}", DAY + timedelta(days=off))
        import icalendar
        got = lambda user: {str(v["SUMMARY"]) for v in icalendar.Calendar.from_ical(self.get("/planner/calendar.ics", user).content).walk("VEVENT")}
        self.assertEqual(got(self.alice), {"e-30", "e366", "e367"})            # window: Auckland today-31 .. today+366
        self.assertEqual(got(self.bella), {"e-31", "e-30", "e366"})            # window: LA today-31 .. today+366

    def test_timed_event_is_exported_as_the_matching_utc_instant(self):
        import icalendar
        self.event(self.acme, "Walkthrough", DAY + timedelta(days=5), start_time=T(9, 0), end_time=T(10, 0))
        v = icalendar.Calendar.from_ical(self.get("/planner/calendar.ics", self.alice).content).walk("VEVENT")[0]
        expected = datetime.combine(DAY + timedelta(days=5), T(9, 0), tzinfo=ZoneInfo("Pacific/Auckland")).astimezone(UTC)
        self.assertEqual(v["DTSTART"].dt, expected)

    def test_switching_accounts_switches_the_clock(self):
        self.assertEqual(self.get("/", self.carol).context["today"], TOMORROW)         # carol's first membership: Acme
        self.post("/accounts/switch/", {"account_id": self.beta.pk})
        self.assertEqual(self.get("/").context["today"], DAY)

    def test_the_zone_does_not_leak_past_the_request(self):
        self.get("/", self.alice)
        self.assertEqual(timezone.get_current_timezone_name(), settings.TIME_ZONE)
        self.client.logout()
        self.get("/accounts/login/")
        self.assertEqual(timezone.get_current_timezone_name(), settings.TIME_ZONE)

    def test_a_corrupt_stored_zone_degrades_to_utc_instead_of_failing(self):
        Account.objects.filter(pk=self.acme.pk).update(timezone="Nope/Zone")
        r = self.get("/", self.alice)
        self.assertEqual((r.status_code, r.context["today"]), (200, DAY))


class ReminderJobTests(TZBase):
    D1 = D(2026, 12, 1)

    def setUp(self):
        super().setUp()
        for account, prop, title in ((self.acme, self.maple, "ACME boiler"), (self.beta, self.lake, "BETA chiller")):
            with use_account(account):
                t = MaintenanceTask.objects.create(property=prop, title=title, start_date=self.D1, recurrence_rule="",
                                                   remind_days_before=[0])
                ensure_first_occurrence(t)

    def run_at(self, when, **kw):
        out = StringIO()
        with mock.patch("django.utils.timezone.now", return_value=when):
            call_command("send_maintenance_reminders", stdout=out, **kw)
        return out.getvalue()

    N1 = datetime(2026, 11, 30, 20, 0, tzinfo=UTC)      # Auckland Dec 1 09:00   |  Los Angeles Nov 30 12:00
    N2 = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)        # Auckland Dec 1 22:00   |  Los Angeles Dec 1 01:00
    N3 = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)       # Auckland Dec 2 01:00   |  Los Angeles Dec 1 04:00

    def test_each_account_is_reminded_on_its_own_local_day(self):
        out = self.run_at(self.N1)
        self.assertEqual([m.subject for m in mail.outbox if "due today" in m.subject and "ACME" in m.subject].__len__(), 1)
        self.assertEqual(len(mail.outbox), 1)                                          # Los Angeles has not reached Dec 1
        self.assertIn("[Pacific/Auckland, 2026-12-01]", out); self.assertIn("[America/Los_Angeles, 2026-11-30]", out)
        self.run_at(self.N2)
        self.assertEqual([("BETA" in m.subject, "ACME" in m.subject) for m in mail.outbox], [(False, True), (True, False)])
        self.run_at(self.N2)
        self.assertEqual(len(mail.outbox), 2, "rerunning is idempotent; Auckland is not reminded twice")

    def test_overdue_arrives_on_the_local_day_after(self):
        self.run_at(self.N1); self.run_at(self.N2)
        self.run_at(self.N3)
        self.assertEqual(len(mail.outbox), 3)
        self.assertIn("ACME", mail.outbox[2].subject); self.assertIn("1 day overdue", mail.outbox[2].subject)

    def test_today_flag_overrides_every_account(self):
        self.run_at(self.N1, today=self.D1)
        self.assertEqual(len(mail.outbox), 2)

    def test_dry_run_reports_each_accounts_day_and_changes_nothing(self):
        out = self.run_at(self.N1, dry_run=True)
        self.assertEqual((len(mail.outbox), Reminder.all_tenants.count()), (0, 0))
        self.assertIn("would_send", out); self.assertIn("America/Los_Angeles", out)

    def test_utc_accounts_behave_exactly_as_before(self):
        Account.objects.update(timezone="UTC")
        self.run_at(self.N1); self.assertEqual(len(mail.outbox), 0)                    # still Nov 30 in UTC
        self.run_at(datetime(2026, 12, 1, 0, 0, tzinfo=UTC)); self.assertEqual(len(mail.outbox), 2)


class SettingsPageTests(TZBase):
    def setUp(self):
        super().setUp()
        self.mgr = User.objects.create_user("mgr@acme.test", PW)
        Membership.objects.create(user=self.mgr, account=self.acme, role=Membership.Role.MANAGER)

    def tz(self, account):
        return Account.objects.get(pk=account.pk).timezone

    def test_owner_sees_the_form_with_current_zone_and_local_time(self):
        r = self.get("/accounts/settings/", self.alice)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '<option value="Pacific/Auckland" selected>')
        local = NOW.astimezone(ZoneInfo("Pacific/Auckland"))
        self.assertContains(r, f"{local:%-I:%M %p}"); self.assertContains(r, "Local time in <strong>Pacific/Auckland</strong>")

    def test_saving_changes_the_name_and_the_clock_from_the_next_request(self):
        r = self.post("/accounts/settings/", {"name": "Acme Rentals LLC", "timezone": "America/Chicago"}, self.alice)
        self.assertRedirects(r, "/accounts/settings/", fetch_redirect_response=False)
        acct = Account.objects.get(pk=self.acme.pk)
        self.assertEqual((acct.name, acct.timezone), ("Acme Rentals LLC", "America/Chicago"))
        self.assertEqual(self.get("/", self.alice).context["today"], DAY)              # was tomorrow while in Auckland

    def test_invalid_input_is_a_form_error_and_changes_nothing(self):
        for data, field in (({"name": "X", "timezone": "Mars/Base"}, "timezone"), ({"name": "X", "timezone": "US/Eastern"}, "timezone"),
                            ({"name": "X", "timezone": ""}, "timezone"), ({"name": "", "timezone": "Asia/Tokyo"}, "name"),
                            ({"name": "X", "timezone": "../../etc/passwd"}, "timezone")):
            r = self.post("/accounts/settings/", data, self.alice)
            self.assertEqual(r.status_code, 200, data); self.assertTrue(r.context["form"].errors.get(field), data)
        acct = Account.objects.get(pk=self.acme.pk)
        self.assertEqual((acct.name, acct.timezone), ("Acme Rentals", "Pacific/Auckland"))

    def test_account_name_is_escaped_everywhere(self):
        self.post("/accounts/settings/", {"name": "<b>x</b>", "timezone": "UTC"}, self.alice)
        r = self.get("/", self.alice)
        self.assertNotContains(r, "<b>x</b>"); self.assertContains(r, "&lt;b&gt;x&lt;/b&gt;")

    def test_only_owners_may_open_or_change_settings(self):
        for user in (self.vic, self.mgr):
            self.assertEqual(self.get("/accounts/settings/", user).status_code, 403)
            self.assertEqual(self.post("/accounts/settings/", {"name": "Hacked", "timezone": "UTC"}, user).status_code, 403)
        self.assertEqual(Account.objects.get(pk=self.acme.pk).name, "Acme Rentals")
        self.client.logout()
        r = self.client.get("/accounts/settings/")
        self.assertEqual(r.status_code, 302); self.assertIn("/accounts/login/", r["Location"])

    def test_a_request_can_only_ever_change_the_active_account(self):
        self.post(f"/accounts/settings/?pk={self.acme.pk}&account_id={self.acme.pk}", {"name": "Beta Renamed", "timezone": "Asia/Tokyo"}, self.bella)
        self.assertEqual((self.tz(self.beta), Account.objects.get(pk=self.acme.pk).name), ("Asia/Tokyo", "Acme Rentals"))
        self.assertEqual(self.tz(self.acme), "Pacific/Auckland")

    def test_role_is_checked_per_account_for_users_in_both(self):
        self.assertEqual(self.get("/accounts/settings/", self.carol).status_code, 200)          # owner in Acme
        self.post("/accounts/switch/", {"account_id": self.beta.pk})
        self.assertEqual(self.get("/accounts/settings/").status_code, 403)                      # viewer in Beta

    def test_settings_link_is_only_shown_to_owners(self):
        self.assertContains(self.get("/", self.alice), 'href="/accounts/settings/"')
        for user in (self.vic, self.mgr):
            self.assertNotContains(self.get("/", user), 'href="/accounts/settings/"')

    def test_a_legacy_stored_zone_stays_selectable_and_saveable(self):
        Account.objects.filter(pk=self.acme.pk).update(timezone="US/Eastern")
        self.assertContains(self.get("/accounts/settings/", self.alice), '<option value="US/Eastern" selected>')
        r = self.post("/accounts/settings/", {"name": "Renamed", "timezone": "US/Eastern"}, self.alice)
        self.assertEqual(r.status_code, 302); self.assertEqual(Account.objects.get(pk=self.acme.pk).name, "Renamed")


class SignupTimezoneTests(TZBase):
    def signup(self, email, **extra):
        self.client.logout()
        return self.client.post("/accounts/signup/", {"account_name": "Gamma", "email": email, "password1": PW, "password2": PW, **extra})

    def test_page_offers_canonical_zones_and_preselects_the_browsers_own(self):
        body = self.client.get("/accounts/signup/").content.decode()
        self.assertIn('<option value="America/New_York">', body); self.assertIn('<option value="UTC"', body)
        self.assertNotIn("US/Eastern", body); self.assertNotIn("Etc/GMT", body)
        self.assertIn("Intl.DateTimeFormat", body); self.assertIn('id_timezone', body)

    def test_chosen_zone_is_saved_and_missing_means_utc(self):
        self.signup("a@g.test", timezone="Europe/London")
        self.assertEqual(Account.objects.get(memberships__user__email="a@g.test").timezone, "Europe/London")
        self.signup("b@g.test")
        self.assertEqual(Account.objects.get(memberships__user__email="b@g.test").timezone, "UTC")

    def test_bad_zones_are_rejected_without_creating_anything(self):
        for bad in ("Nope/Zone", "US/Eastern", "../../etc/passwd"):
            r = self.signup("c@g.test", timezone=bad)
            self.assertEqual(r.status_code, 200, bad); self.assertTrue(r.context["form"].errors.get("timezone"), bad)
        self.assertFalse(User.objects.filter(email="c@g.test").exists())

    def test_new_account_starts_on_its_own_clock(self):
        self.signup("d@g.test", timezone="Pacific/Kiritimati")                           # +14: tomorrow at 20:00Z
        with at():
            self.assertEqual(self.client.get("/").context["today"], TOMORROW)
