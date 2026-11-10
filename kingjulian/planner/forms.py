from django import forms

from contacts.models import Contact
from properties.models import Property

from .models import Event

_DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
_TIME = forms.TimeInput(attrs={"type": "time"}, format="%H:%M")


class EventForm(forms.ModelForm):
    class Meta:
        model = Event
        fields = ["title", "kind", "property", "contact", "starts_on", "start_time", "ends_on", "end_time", "notes"]
        widgets = {"starts_on": _DATE, "ends_on": _DATE, "start_time": _TIME, "end_time": _TIME,
                   "notes": forms.Textarea(attrs={"rows": 3})}
        labels = {"starts_on": "Date", "start_time": "Start time", "ends_on": "Last day (multi-day only)",
                  "end_time": "End time"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["property"].queryset = Property.objects.active()
        self.fields["contact"].queryset = Contact.objects.active()
