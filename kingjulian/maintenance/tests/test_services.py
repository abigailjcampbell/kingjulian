from datetime import date as D
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from core.tenancy import use_account
from core.tests.base import World
from maintenance.models import MaintenanceTask, TaskOccurrence
from maintenance.services import (AlreadyClosed, complete_occurrence, ensure_first_occurrence,
                                  skip_occurrence)

OPEN, DONE, SKIPPED = (TaskOccurrence.Status.OPEN, TaskOccurrence.Status.DONE, TaskOccurrence.Status.SKIPPED)


class ServiceBase(World):
    def make(self, rule="FREQ=MONTHLY", start=D(2026, 12, 1), account=None, **kw):
        account = account or self.acme
        defaults = dict(property=self.maple, title="Boiler service", start_date=start, recurrence_rule=rule)
        with use_account(account):
            task = MaintenanceTask.objects.create(**{**defaults, **kw})
            ensure_first_occurrence(task)
        return task

    def occs(self, task, account=None):
        with use_account(account or self.acme):
            return list(TaskOccurrence.objects.filter(task=task))


class SchedulingTests(ServiceBase):
    def test_first_occurrence_created_once(self):
        task = self.make()
        with use_account(self.acme):
            self.assertIsNone(ensure_first_occurrence(task))
        occs = self.occs(task)
        self.assertEqual([(o.due_date, o.status) for o in occs], [(D(2026, 12, 1), OPEN)])

    def test_complete_records_history_and_opens_next(self):
        task = self.make()
        with use_account(self.acme):
            occ = TaskOccurrence.objects.get(task=task)
            done, nxt = complete_occurrence(occ, completed_on=D(2026, 12, 3), user=self.alice,
                                            cost=Decimal("120.50"), notes="Replaced igniter")
        self.assertEqual((done.status, done.completed_on, done.cost, done.completed_by), (DONE, D(2026, 12, 3), Decimal("120.50"), self.alice))
        self.assertEqual((nxt.due_date, nxt.status), (D(2027, 1, 1), OPEN))
        self.assertEqual([(o.due_date, o.status) for o in self.occs(task)], [(D(2026, 12, 1), DONE), (D(2027, 1, 1), OPEN)])

    def test_exactly_one_open_occurrence_after_many_cycles(self):
        task = self.make(rule="FREQ=WEEKLY", start=D(2026, 12, 1))
        with use_account(self.acme):
            for i in range(10):
                complete_occurrence(TaskOccurrence.objects.get(task=task, status=OPEN), completed_on=D(2026, 12, 1))
        occs = self.occs(task)
        self.assertEqual(len(occs), 11)
        self.assertEqual(sum(o.status == OPEN for o in occs), 1)
        self.assertEqual(len({o.due_date for o in occs}), 11)

    def test_double_completion_is_refused_and_creates_no_duplicate(self):
        task = self.make()
        with use_account(self.acme):
            occ = TaskOccurrence.objects.get(task=task)
            complete_occurrence(occ, completed_on=D(2026, 12, 1))
            with self.assertRaises(AlreadyClosed):
                complete_occurrence(occ, completed_on=D(2026, 12, 1))   # stale in-memory copy, still says open
        self.assertEqual(len(self.occs(task)), 2)

    def test_database_forbids_two_open_occurrences(self):
        task = self.make()
        with use_account(self.acme):
            with self.assertRaises(IntegrityError), transaction.atomic():
                TaskOccurrence.objects.create(task=task, due_date=D(2027, 6, 1))

    def test_one_off_task_ends_after_completion(self):
        task = self.make(rule="")
        with use_account(self.acme):
            _, nxt = complete_occurrence(TaskOccurrence.objects.get(task=task), completed_on=D(2026, 12, 1))
        self.assertIsNone(nxt)
        self.assertEqual(sum(o.status == OPEN for o in self.occs(task)), 0)

    def test_skip_keeps_schedule_without_marking_work_done(self):
        task = self.make(start=D(2026, 12, 1))
        with use_account(self.acme):
            skipped, nxt = skip_occurrence(TaskOccurrence.objects.get(task=task), today=D(2026, 12, 1))
        self.assertEqual((skipped.status, skipped.completed_on), (SKIPPED, None))
        self.assertEqual(nxt.due_date, D(2027, 1, 1))

    def test_after_completion_mode_counts_from_completion_day(self):
        task = self.make(rule="FREQ=MONTHLY;INTERVAL=3", start=D(2026, 12, 1),
                         schedule_mode=MaintenanceTask.ScheduleMode.AFTER_COMPLETION)
        with use_account(self.acme):
            _, nxt = complete_occurrence(TaskOccurrence.objects.get(task=task), completed_on=D(2026, 12, 20))
        self.assertEqual(nxt.due_date, D(2027, 3, 20))

    def test_late_fixed_completion_does_not_pile_up_overdue_copies(self):
        task = self.make(rule="FREQ=MONTHLY", start=D(2026, 1, 1))
        with use_account(self.acme):
            _, nxt = complete_occurrence(TaskOccurrence.objects.get(task=task), completed_on=D(2026, 5, 20))
        self.assertEqual(nxt.due_date, D(2026, 6, 1))

    def test_next_never_collides_with_a_rescheduled_occurrence(self):
        task = self.make(rule="FREQ=MONTHLY", start=D(2026, 12, 1))
        with use_account(self.acme):
            occ = TaskOccurrence.objects.get(task=task)
            TaskOccurrence.objects.create(task=task, due_date=D(2027, 1, 1), status=DONE)   # a past row
            _, nxt = complete_occurrence(occ, completed_on=D(2026, 12, 1))
        self.assertEqual(nxt.due_date, D(2027, 2, 1))

    def test_archived_task_does_not_reschedule(self):
        task = self.make()
        with use_account(self.acme):
            task.archive()
            occ = TaskOccurrence.objects.get(task=task)
            _, nxt = complete_occurrence(occ, completed_on=D(2026, 12, 1))
        self.assertIsNone(nxt)


class ModelGuardTests(ServiceBase):
    def test_garbage_rule_cannot_be_saved(self):
        with use_account(self.acme):
            with self.assertRaises(ValidationError):
                MaintenanceTask.objects.create(property=self.maple, title="x", start_date=D(2026, 12, 1),
                                               recurrence_rule="FREQ=HOURLY")

    def test_rule_is_normalised_on_save(self):
        task = self.make(rule="rrule:freq=monthly;interval=3")
        self.assertEqual(task.recurrence_rule, "FREQ=MONTHLY;INTERVAL=3")

    def test_links_must_agree_with_property_and_unit(self):
        with use_account(self.acme):
            for kw in (dict(unit=self.maple_a, property=self.oak),         # unit in another property
                       dict(item=self.fridge, property=self.maple),         # item in another property
                       dict(item=self.heater, unit=self.maple_b, property=self.maple)):   # item in another unit
                with self.assertRaises(ValidationError, msg=str(kw)):
                    MaintenanceTask.objects.create(title="x", start_date=D(2026, 12, 1), **kw)

    def test_cross_tenant_links_are_refused(self):
        from core.tenancy import TenantViolation
        with use_account(self.acme):
            with self.assertRaises(TenantViolation):
                MaintenanceTask.objects.create(property=self.lake, title="x", start_date=D(2026, 12, 1))
            with self.assertRaises(TenantViolation):
                MaintenanceTask.objects.create(property=self.maple, assigned_contact=self.beta_contact,
                                               title="x", start_date=D(2026, 12, 1))
