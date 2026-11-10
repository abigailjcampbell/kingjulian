import builtins

from django.core.exceptions import ValidationError
from django.db import models

from contacts.models import Contact
from core.models import TenantModel
from properties.models import Property

MAX_SPAN_DAYS = 366


def _fmt_time(t):
    return t.strftime("%-I:%M %p")


class Event(TenantModel):
    """A manual calendar entry: inspection, showing, lease milestone, anything dated.

    Dates are plain calendar dates; times are optional and "floating" (no timezone) because accounts
    don't have one yet. Task due dates and warranty expirations are NOT stored here: the calendar
    computes them from their own tables (see planner.services.calendar_entries).
    """

    class Kind(models.TextChoices):
        INSPECTION = "inspection", "Inspection"
        SHOWING = "showing", "Showing"
        LEASE = "lease", "Lease milestone"
        OTHER = "other", "Other"

    property = models.ForeignKey(Property, on_delete=models.PROTECT, null=True, blank=True, related_name="events")
    contact = models.ForeignKey(Contact, on_delete=models.SET_NULL, null=True, blank=True, related_name="events")
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.OTHER)
    title = models.CharField(max_length=200)
    starts_on = models.DateField()
    start_time = models.TimeField(null=True, blank=True)
    ends_on = models.DateField(null=True, blank=True, help_text="Only for multi-day events.")
    end_time = models.TimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["starts_on", "start_time", "id"]
        indexes = [models.Index(fields=["account", "starts_on"])]

    def __str__(self):
        return self.title

    # `property` is a model field here, so reach the builtin decorator explicitly.
    @builtins.property
    def last_day(self):
        return self.ends_on or self.starts_on

    @builtins.property
    def date_label(self):
        if self.last_day == self.starts_on:
            return f"{self.starts_on:%b %-d, %Y}"
        return f"{self.starts_on:%b %-d} – {self.last_day:%b %-d, %Y}"

    @builtins.property
    def time_label(self):
        if not self.start_time:
            return "All day"
        return _fmt_time(self.start_time) + (f" – {_fmt_time(self.end_time)}" if self.end_time else "")

    def validate_consistency(self):
        errors = {}
        if self.starts_on and self.ends_on:
            if self.ends_on < self.starts_on:
                errors["ends_on"] = "The end date can't be before the start date."
            elif (self.ends_on - self.starts_on).days > MAX_SPAN_DAYS:
                errors["ends_on"] = f"Events can span at most {MAX_SPAN_DAYS} days."
        if self.end_time and not self.start_time:
            errors["end_time"] = "Add a start time first, or clear the end time."
        elif (self.start_time and self.end_time and self.end_time <= self.start_time
              and (not self.ends_on or self.ends_on == self.starts_on)):
            errors["end_time"] = "The end time must be after the start time."
        if errors:
            raise ValidationError(errors)

    def clean(self):
        super().clean()
        self.validate_consistency()

    def save(self, *args, **kwargs):
        self.validate_consistency()
        super().save(*args, **kwargs)
