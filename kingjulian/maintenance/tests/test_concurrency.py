"""Real threads, real commits (TransactionTestCase): two requests race to complete the same occurrence."""
import threading
from datetime import date as D

from django.db import connection
from django.test import TransactionTestCase

from accounts.models import Account
from core.tenancy import use_account
from maintenance.models import MaintenanceTask, TaskOccurrence
from maintenance.services import AlreadyClosed, complete_occurrence, ensure_first_occurrence
from properties.models import Property

ROUNDS = 12


class ConcurrentCompletionTests(TransactionTestCase):
    def setUp(self):
        self.account = Account.objects.create(name="Race Co")
        with use_account(self.account):
            self.prop = Property.objects.create(name="Race House")

    def _race(self, workers=2):
        with use_account(self.account):
            task = MaintenanceTask.objects.create(property=self.prop, title="Race", start_date=D(2026, 12, 1),
                                                  recurrence_rule="FREQ=MONTHLY")
            occ = ensure_first_occurrence(task)
        barrier, results = threading.Barrier(workers), []

        def work():
            try:
                with use_account(self.account.pk):
                    stale = TaskOccurrence.objects.get(pk=occ.pk)      # everyone read it while it was open
                    barrier.wait(timeout=10)
                    complete_occurrence(stale, completed_on=D(2026, 12, 1))
                results.append("ok")
            except AlreadyClosed:
                results.append("already_closed")
            except Exception as exc:                                    # noqa: BLE001
                results.append(type(exc).__name__)
            finally:
                connection.close()

        threads = [threading.Thread(target=work) for _ in range(workers)]
        [t.start() for t in threads]; [t.join(timeout=20) for t in threads]
        with use_account(self.account):
            rows = list(TaskOccurrence.objects.filter(task=task))
        return sorted(results), rows

    def test_exactly_one_winner_and_exactly_one_next_occurrence(self):
        for i in range(ROUNDS):
            results, rows = self._race()
            self.assertEqual(results, ["already_closed", "ok"], f"round {i}")
            self.assertEqual(len(rows), 2, f"round {i}")
            self.assertEqual(sum(r.status == "open" for r in rows), 1, f"round {i}")

    def test_five_way_race_still_has_one_winner(self):
        results, rows = self._race(workers=5)
        self.assertEqual(results, ["already_closed"] * 4 + ["ok"])
        self.assertEqual((len(rows), sum(r.status == "open" for r in rows)), (2, 1))
