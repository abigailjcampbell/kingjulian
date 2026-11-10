import builtins

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from contacts.models import Contact
from core.models import TenantModel
from inventory.models import InventoryItem
from properties.models import Property, Unit

from . import recurrence


def default_remind_days():
    return [7, 1]


class MaintenanceTask(TenantModel):
    """The template: what needs doing, where, how often. Each due date is a TaskOccurrence."""

    class ScheduleMode(models.TextChoices):
        FIXED = recurrence.FIXED, "Fixed schedule (keeps the original cadence)"
        AFTER_COMPLETION = recurrence.AFTER_COMPLETION, "Repeat after each completion"

    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="maintenance_tasks")
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, null=True, blank=True,
                             related_name="maintenance_tasks")
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, null=True, blank=True,
                             related_name="maintenance_tasks")
    assigned_contact = models.ForeignKey(Contact, on_delete=models.SET_NULL, null=True, blank=True,
                                         related_name="maintenance_tasks")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    start_date = models.DateField(help_text="First due date. Also the anchor for fixed schedules.")
    recurrence_rule = models.CharField(max_length=200, blank=True)   # RRULE; '' = one-off
    schedule_mode = models.CharField(max_length=20, choices=ScheduleMode.choices, default=ScheduleMode.FIXED)
    remind_days_before = models.JSONField(default=default_remind_days, blank=True)

    class Meta:
        ordering = ["title"]
        indexes = [models.Index(fields=["account", "property"])]

    def __str__(self):
        return self.title

    # `property` is a model field here and shadows the builtin decorator inside this class body.
    @builtins.property
    def schedule_label(self):
        return recurrence.describe(self.recurrence_rule, self.schedule_mode)

    def validate_consistency(self):
        errors = {}
        if self.property_id:
            if self.unit_id and self.unit.property_id != self.property_id:
                errors["unit"] = "That unit does not belong to the selected property."
            if self.item_id and self.item.property_id != self.property_id:
                errors["item"] = "That inventory item does not belong to the selected property."
            elif self.item_id and self.unit_id and self.item.unit_id and self.item.unit_id != self.unit_id:
                errors["item"] = "That inventory item is in a different unit."
        try:
            self.recurrence_rule = recurrence.normalize_rule(self.recurrence_rule)
        except ValueError as exc:
            errors["recurrence_rule"] = str(exc)
        days = self.remind_days_before
        if not (isinstance(days, list) and all(isinstance(d, int) and 0 <= d <= 365 for d in days)):
            errors["remind_days_before"] = "Must be a list of whole days between 0 and 365."
        if errors:
            raise ValidationError(errors)

    def clean(self):
        super().clean()
        self.validate_consistency()

    def save(self, *args, **kwargs):
        self.validate_consistency()      # enforced outside forms too; also normalises the rule
        super().save(*args, **kwargs)


class TaskOccurrence(TenantModel):
    """One due date of a task, with its own completion, cost and notes (the history)."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        DONE = "done", "Done"
        SKIPPED = "skipped", "Skipped"

    task = models.ForeignKey(MaintenanceTask, on_delete=models.PROTECT, related_name="occurrences")
    due_date = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    completed_on = models.DateField(null=True, blank=True)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name="+")
    cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["due_date", "id"]
        constraints = [
            models.UniqueConstraint(fields=["task", "due_date"], name="occurrence_task_due_unique"),
            # The database itself guarantees a task never has two open occurrences.
            models.UniqueConstraint(fields=["task"], condition=Q(status="open"),
                                    name="occurrence_one_open_per_task"),
        ]
        indexes = [models.Index(fields=["account", "status", "due_date"])]

    def __str__(self):
        return f"{self.task.title} ({self.due_date:%b %d, %Y})"

    @property
    def due_label(self):
        if self.status != self.Status.OPEN:
            return self.get_status_display()
        days = (self.due_date - timezone.localdate()).days
        if days < 0:
            return f"{-days} day{'s' if days != -1 else ''} overdue"
        if days == 0:
            return "Due today"
        return f"In {days} day{'s' if days != 1 else ''}"


class Reminder(TenantModel):
    """One planned notification. The unique key makes the daily job idempotent."""

    class Kind(models.TextChoices):
        UPCOMING = "upcoming", "Upcoming"
        OVERDUE = "overdue", "Overdue"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        SUPERSEDED = "superseded", "Superseded"      # a later reminder or a state change made it moot
        FAILED = "failed", "Failed"                  # gave up after MAX_ATTEMPTS
        NO_RECIPIENTS = "no_recipients", "No recipients"

    occurrence = models.ForeignKey(TaskOccurrence, on_delete=models.CASCADE, related_name="reminders")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    remind_on = models.DateField()
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING)
    sent_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True)
    recipient_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["remind_on", "id"]
        constraints = [
            models.UniqueConstraint(fields=["occurrence", "kind", "remind_on"], name="reminder_unique_slot"),
        ]
        indexes = [models.Index(fields=["account", "status", "remind_on"])]
