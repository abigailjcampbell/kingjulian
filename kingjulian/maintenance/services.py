"""Scheduling operations. Always call these instead of editing occurrence status by hand."""
from django.db import transaction
from django.utils import timezone

from .models import MaintenanceTask, TaskOccurrence
from .recurrence import next_due


class AlreadyClosed(Exception):
    """The occurrence was already completed or skipped (e.g. a double-click or a concurrent request)."""


def ensure_first_occurrence(task: MaintenanceTask):
    """Create the first open occurrence at task.start_date if the task has none yet."""
    if TaskOccurrence.objects.filter(task=task).exists():
        return None
    return TaskOccurrence.objects.create(account_id=task.account_id, task=task, due_date=task.start_date)


def _close(occurrence, status, *, closed_on, user, cost, notes):
    with transaction.atomic():
        # Lock the row so two simultaneous completions can't both spawn a next occurrence.
        occ = (TaskOccurrence.objects.select_for_update(of=("self",))
               .select_related("task").get(pk=occurrence.pk))
        if occ.status != TaskOccurrence.Status.OPEN:
            raise AlreadyClosed(f"{occ} is already {occ.status}.")
        occ.status = status
        occ.completed_on = closed_on if status == TaskOccurrence.Status.DONE else None
        occ.completed_by = user
        if cost is not None:
            occ.cost = cost
        if notes:
            occ.notes = notes
        occ.save()
        return occ, _schedule_next(occ, closed_on)


def _schedule_next(occ, closed_on):
    task = occ.task
    if task.is_archived:
        return None
    due = next_due(task.recurrence_rule, mode=task.schedule_mode, anchor=task.start_date,
                   scheduled=occ.due_date, completed_on=closed_on)
    # Never collide with an existing occurrence (e.g. one the user rescheduled); step past it.
    for _ in range(10):
        if due is None or not TaskOccurrence.objects.filter(task=task, due_date=due).exists():
            break
        due = next_due(task.recurrence_rule, mode=task.schedule_mode, anchor=task.start_date,
                       scheduled=due, completed_on=due)
    if due is None:
        return None
    return TaskOccurrence.objects.create(account_id=task.account_id, task=task, due_date=due)


def complete_occurrence(occurrence, *, completed_on=None, user=None, cost=None, notes=""):
    """Mark done and open the next occurrence if the task repeats. Returns (done, next_or_None)."""
    return _close(occurrence, TaskOccurrence.Status.DONE, closed_on=completed_on or timezone.localdate(),
                  user=user, cost=cost, notes=notes)


def skip_occurrence(occurrence, *, today=None, user=None, notes=""):
    """Skip this one (no work done) but keep the schedule going."""
    return _close(occurrence, TaskOccurrence.Status.SKIPPED, closed_on=today or timezone.localdate(),
                  user=user, cost=None, notes=notes)
