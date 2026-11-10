from django import forms
from django.utils import timezone

from contacts.models import Contact
from inventory.models import InventoryItem
from properties.models import Property, Unit

from . import recurrence
from .models import MaintenanceTask, TaskOccurrence

CUSTOM = "__custom__"
_DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


def parse_remind_days(text: str) -> list[int]:
    out = set()
    for token in text.replace(",", " ").split():
        try:
            n = int(token)
        except ValueError:
            raise forms.ValidationError(f"'{token}' is not a whole number of days.")
        if not 0 <= n <= 365:
            raise forms.ValidationError("Reminder days must be between 0 and 365.")
        out.add(n)
    return sorted(out, reverse=True)


class TaskForm(forms.ModelForm):
    repeat = forms.ChoiceField(choices=recurrence.PRESETS + [(CUSTOM, "Custom rule…")], required=False,
                               label="Repeats")
    custom_rule = forms.CharField(required=False, max_length=200, label="Custom rule",
                                  help_text="Only used with 'Custom rule…'. Example: FREQ=WEEKLY;BYDAY=MO,TH")
    remind_days = forms.CharField(required=False, label="Remind me (days before)",
                                  help_text="Comma-separated, e.g. 7, 1. Use 0 for the due date. Blank = no reminders.")

    class Meta:
        model = MaintenanceTask
        fields = ["property", "unit", "item", "assigned_contact", "title", "description",
                  "start_date", "schedule_mode"]
        widgets = {"start_date": _DATE, "description": forms.Textarea(attrs={"rows": 3})}

    field_order = ["title", "property", "unit", "item", "assigned_contact", "start_date", "repeat",
                   "custom_rule", "schedule_mode", "remind_days", "description"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["property"].queryset = Property.objects.active()
        self.fields["unit"].queryset = Unit.objects.active().select_related("property")
        self.fields["item"].queryset = InventoryItem.objects.active().select_related("property")
        self.fields["assigned_contact"].queryset = Contact.objects.active()
        rule = self.instance.recurrence_rule if self.instance.pk else ""
        if rule in recurrence.PRESET_LABELS:
            self.fields["repeat"].initial = rule
        elif rule:
            self.fields["repeat"].initial = CUSTOM
            self.fields["custom_rule"].initial = rule
        days = self.instance.remind_days_before if self.instance.pk else [7, 1]
        self.fields["remind_days"].initial = ", ".join(str(d) for d in days)

    def clean(self):
        cleaned = super().clean()
        choice = cleaned.get("repeat") or ""
        field = "custom_rule" if choice == CUSTOM else "repeat"
        rule = (cleaned.get("custom_rule") or "") if choice == CUSTOM else choice
        if choice == CUSTOM and not rule.strip():
            self.add_error("custom_rule", "Enter a rule, or pick a preset.")
        try:
            rule = recurrence.normalize_rule(rule)
        except ValueError as exc:
            self.add_error(field, str(exc))
            rule = ""
        try:
            days = parse_remind_days(cleaned.get("remind_days") or "")
        except forms.ValidationError as exc:
            self.add_error("remind_days", exc)
            days = []
        # Not in Meta.fields, so set on the instance; the model's clean() re-validates both.
        self.instance.recurrence_rule = rule
        self.instance.remind_days_before = days
        return cleaned


class OccurrenceForm(forms.ModelForm):
    class Meta:
        model = TaskOccurrence
        fields = ["due_date", "cost", "notes"]
        widgets = {"due_date": _DATE, "notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.status == TaskOccurrence.Status.OPEN:
            del self.fields["cost"]                       # cost belongs to completed work
        else:
            self.fields["due_date"].disabled = True       # history keeps its original due date


class CompleteForm(forms.Form):
    completed_on = forms.DateField(widget=_DATE, label="Completed on")
    cost = forms.DecimalField(required=False, min_value=0, max_digits=10, decimal_places=2)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["completed_on"].initial = timezone.localdate()

    def clean_completed_on(self):
        value = self.cleaned_data["completed_on"]
        if value > timezone.localdate():
            raise forms.ValidationError("Completion date can't be in the future.")
        return value
