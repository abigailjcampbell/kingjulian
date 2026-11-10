from datetime import date as D
from io import StringIO
from smtplib import SMTPException
from unittest import mock

from django.core import mail
from django.core.management import call_command

from accounts.models import Account, Membership, User
from core.tenancy import use_account
from core.tests.base import PW, World
from maintenance.models import MaintenanceTask, Reminder, TaskOccurrence
from maintenance.reminders import generate_reminders, recipients_for, send_pending
from maintenance.services import complete_occurrence, ensure_first_occurrence

DUE = D(2026, 12, 1)
S = Reminder.Status


class ReminderBase(World):
    def make(self, account=None, prop=None, title="Boiler service", rule="FREQ=MONTHLY", **kw):
        account = account or self.acme
        with use_account(account):
            task = MaintenanceTask.objects.create(property=prop or self.maple, title=title, start_date=DUE,
                                                  recurrence_rule=rule, **kw)
            ensure_first_occurrence(task)
        return task

    def run_day(self, day, account=None):
        with use_account(account or self.acme):
            return generate_reminders(day), send_pending(day)

    def reminders(self, account=None):
        with use_account(account or self.acme):
            return list(Reminder.objects.all())


class TimelineTests(ReminderBase):
    def test_full_lifecycle_sends_exactly_three_emails(self):
        self.make()
        plan = [  # (day, emails expected that day)
            (D(2026, 11, 23), 0),   # 8 days out: nothing yet
            (D(2026, 11, 24), 1),   # 7 days out
            (D(2026, 11, 24), 0),   # same-day rerun is idempotent
            (D(2026, 11, 28), 0),
            (D(2026, 11, 30), 1),   # 1 day out
            (D(2026, 12, 1), 0),    # due day (0 not configured)
            (D(2026, 12, 2), 1),    # overdue
            (D(2026, 12, 3), 0),    # overdue reminder fires once only
        ]
        for day, expected in plan:
            before = len(mail.outbox)
            self.run_day(day)
            self.assertEqual(len(mail.outbox) - before, expected, f"{day}")
        subjects = [m.subject for m in mail.outbox]
        self.assertIn("due in 7 days", subjects[0]); self.assertIn("due in 1 day", subjects[1])
        self.assertIn("1 day overdue", subjects[2])
        self.assertTrue(all("Boiler service" in s and "Maple Duplex" in s for s in subjects))

    def test_job_started_late_sends_one_email_not_a_burst(self):
        self.make()
        self.run_day(DUE)                                  # first ever run is on the due date
        self.assertEqual(len(mail.outbox), 1)
        by_day = {r.remind_on: r.status for r in self.reminders()}
        self.assertEqual(by_day, {D(2026, 11, 24): S.SUPERSEDED, D(2026, 11, 30): S.SENT})

    def test_unsent_earlier_reminder_is_retired_when_a_newer_slot_arrives(self):
        self.make()
        with mock.patch("maintenance.reminders.EmailMessage.send", side_effect=SMTPException("outage")):
            self.run_day(D(2026, 11, 24))                  # 7-day reminder fails and stays pending
        self.assertEqual([r.status for r in self.reminders()], [S.PENDING])
        self.run_day(D(2026, 11, 30))                      # outage over; the 1-day slot now exists too
        self.assertEqual(len(mail.outbox), 1)              # one email, not a stale 7-day one plus a 1-day one
        self.assertIn("due in 1 day", mail.outbox[0].subject)
        self.assertEqual({r.remind_on: r.status for r in self.reminders()},
                         {D(2026, 11, 24): S.SUPERSEDED, D(2026, 11, 30): S.SENT})

    def test_overdue_supersedes_unsent_upcoming(self):
        self.make()
        with use_account(self.acme):
            generate_reminders(D(2026, 11, 30))            # 1-day reminder planned but job never sent it
        self.run_day(D(2026, 12, 3))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("overdue", mail.outbox[0].subject)
        self.assertEqual(sorted((r.kind, r.status) for r in self.reminders() if r.remind_on == D(2026, 11, 30)),
                         [("upcoming", S.SUPERSEDED)])

    def test_custom_offsets_including_due_day(self):
        self.make(remind_days_before=[0])
        self.run_day(D(2026, 11, 30)); self.assertEqual(len(mail.outbox), 0)
        self.run_day(DUE);             self.assertEqual(len(mail.outbox), 1)
        self.assertIn("due today", mail.outbox[0].subject)

    def test_no_offsets_means_no_upcoming_reminders_but_overdue_still_fires(self):
        self.make(remind_days_before=[])
        self.run_day(D(2026, 11, 30)); self.assertEqual(len(mail.outbox), 0)
        self.run_day(D(2026, 12, 2));  self.assertEqual(len(mail.outbox), 1)

    def test_completing_before_send_cancels_the_reminder(self):
        task = self.make()
        with use_account(self.acme):
            generate_reminders(D(2026, 11, 24))
            complete_occurrence(TaskOccurrence.objects.get(task=task, status="open"), completed_on=D(2026, 11, 24))
        self.run_day(D(2026, 11, 24))
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual({r.status for r in self.reminders()}, {S.SUPERSEDED})

    def test_archived_task_gets_no_reminders(self):
        task = self.make()
        with use_account(self.acme):
            generate_reminders(D(2026, 11, 24)); task.archive()
        self.run_day(D(2026, 11, 24)); self.run_day(D(2026, 12, 5))
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(len(self.reminders()), 1)          # the one planned before archiving, now superseded
        self.assertEqual(self.reminders()[0].status, S.SUPERSEDED)

    def test_next_occurrence_gets_its_own_reminders(self):
        task = self.make()
        with use_account(self.acme):
            complete_occurrence(TaskOccurrence.objects.get(task=task, status="open"), completed_on=DUE)
        self.run_day(D(2026, 12, 25))                       # next due 2027-01-01, 7 days out
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("due in 7 days", mail.outbox[0].subject)


class RecipientTests(ReminderBase):
    def test_only_owners_and_managers_of_that_account_are_emailed(self):
        self.make()
        self.run_day(D(2026, 11, 24))
        self.assertEqual(len(mail.outbox), 1)
        # alice (owner) and carol (owner in Acme); not vic (viewer) or bella (other tenant)
        self.assertEqual(sorted(mail.outbox[0].bcc), ["alice@acme.test", "carol@both.test"])
        self.assertEqual(mail.outbox[0].to, [])

    def test_manager_role_is_included_and_inactive_users_are_not(self):
        mgr = User.objects.create_user("mgr@acme.test", PW)
        Membership.objects.create(user=mgr, account=self.acme, role=Membership.Role.MANAGER)
        gone = User.objects.create_user("gone@acme.test", PW, is_active=False)
        Membership.objects.create(user=gone, account=self.acme, role=Membership.Role.OWNER)
        self.assertEqual(recipients_for(self.acme.pk), ["alice@acme.test", "carol@both.test", "mgr@acme.test"])

    def test_tenants_never_see_each_others_tasks(self):
        self.make(title="ACME-ONLY boiler")
        self.make(account=self.beta, prop=self.lake, title="BETA-ONLY chiller")
        call_command("send_maintenance_reminders", today=D(2026, 11, 24), stdout=StringIO())
        self.assertEqual(len(mail.outbox), 2)
        by_title = {("ACME" if "ACME-ONLY" in m.subject else "BETA"): m for m in mail.outbox}
        self.assertEqual(by_title["ACME"].bcc, ["alice@acme.test", "carol@both.test"])
        self.assertEqual(by_title["BETA"].bcc, ["bella@beta.test"])      # carol is only a viewer in Beta
        for m in mail.outbox:
            text = m.subject + m.body
            self.assertFalse("ACME-ONLY" in text and "BETA-ONLY" in text)
        self.assertNotIn("Maple", by_title["BETA"].subject + by_title["BETA"].body)
        self.assertNotIn("Lakeview", by_title["ACME"].subject + by_title["ACME"].body)

    def test_account_with_no_eligible_recipients_is_recorded_not_emailed(self):
        empty = Account.objects.create(name="Viewers Only")
        v = User.objects.create_user("v@only.test", PW)
        Membership.objects.create(user=v, account=empty, role=Membership.Role.VIEWER)
        with use_account(empty):
            from properties.models import Property
            p = Property.objects.create(name="Lonely House")
        self.make(account=empty, prop=p)
        self.run_day(D(2026, 11, 24), account=empty)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual([r.status for r in self.reminders(empty)], [S.NO_RECIPIENTS])


class DeliveryFailureTests(ReminderBase):
    def test_transient_failure_retries_next_run(self):
        self.make()
        with mock.patch("maintenance.reminders.EmailMessage.send", side_effect=[SMTPException("smtp down"), 1]):
            self.run_day(D(2026, 11, 24))
            r = self.reminders()[0]
            self.assertEqual((r.status, r.attempts, r.last_error), (S.PENDING, 1, "smtp down"))
            self.run_day(D(2026, 11, 25))
        r = self.reminders()[0]
        self.assertEqual((r.status, r.attempts), (S.SENT, 2))

    def test_gives_up_after_max_attempts(self):
        self.make()
        with mock.patch("maintenance.reminders.EmailMessage.send", side_effect=SMTPException("down")) as send:
            for day in range(24, 30):
                self.run_day(D(2026, 11, day))
        self.assertEqual(send.call_count, 3)
        r = self.reminders()[0]
        self.assertEqual((r.status, r.attempts), (S.FAILED, 3))

    def test_one_failing_reminder_does_not_block_the_others(self):
        self.make(title="First"); self.make(title="Second", prop=self.oak)
        with mock.patch("maintenance.reminders.EmailMessage.send", side_effect=[SMTPException("x"), 1]):
            self.run_day(D(2026, 11, 24))
        self.assertEqual(sorted(r.status for r in self.reminders()), [S.PENDING, S.SENT])


class CommandTests(ReminderBase):
    def test_dry_run_changes_nothing(self):
        self.make()
        out = StringIO()
        call_command("send_maintenance_reminders", today=D(2026, 11, 24), dry_run=True, stdout=out)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(Reminder.all_tenants.count(), 0)
        self.assertIn("would_send", out.getvalue())

    def test_running_twice_is_idempotent(self):
        self.make()
        call_command("send_maintenance_reminders", today=D(2026, 11, 24), stdout=StringIO())
        call_command("send_maintenance_reminders", today=D(2026, 11, 24), stdout=StringIO())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(Reminder.all_tenants.count(), 1)

    def test_email_links_to_the_occurrence(self):
        task = self.make()
        self.run_day(D(2026, 11, 24))
        with use_account(self.acme):
            occ = TaskOccurrence.objects.get(task=task)
        self.assertIn(f"/maintenance/{occ.pk}/", mail.outbox[0].body)
