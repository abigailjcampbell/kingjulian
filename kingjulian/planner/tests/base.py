from datetime import date as D

from core.tenancy import use_account
from core.tests.base import World
from inventory.models import InventoryItem
from maintenance.models import MaintenanceTask, TaskOccurrence
from planner.models import Event


class PlannerWorld(World):
    TODAY = D(2026, 11, 15)          # fixed "today" for the service-level tests

    def occ(self, account, prop, title, due, *, status="open", cost=None, completed_on=None, archived=False, item=None):
        """One task with one occurrence (each open occurrence needs its own task)."""
        with use_account(account):
            task = MaintenanceTask.objects.create(property=prop, title=title, start_date=due, recurrence_rule="", item=item)
            occ = TaskOccurrence.objects.create(
                task=task, due_date=due, status=status, cost=cost,
                completed_on=completed_on or (due if status == "done" else None))
            if archived:
                task.archive()
        return occ

    def item(self, account, prop, name, **kw):
        with use_account(account):
            return InventoryItem.objects.create(property=prop, name=name, **kw)

    def event(self, account, title, starts_on, **kw):
        with use_account(account):
            return Event.objects.create(title=title, starts_on=starts_on, **kw)
