from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from .models import Account
from .timezones import DEFAULT, timezone_choices

User = get_user_model()


class SignupForm(forms.Form):
    account_name = forms.CharField(max_length=120, label="Organization / portfolio name")
    email = forms.EmailField()
    password1 = forms.CharField(widget=forms.PasswordInput, label="Password")
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm password")
    timezone = forms.ChoiceField(required=False, initial=DEFAULT, label="Timezone",
                                 help_text="Decides when a day starts for due dates, overdue status and reminders.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["timezone"].choices = timezone_choices()

    def clean_timezone(self):
        return self.cleaned_data.get("timezone") or DEFAULT

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2:
            if p1 != p2:
                self.add_error("password2", "Passwords do not match.")
            else:
                try:
                    validate_password(p1)
                except forms.ValidationError as exc:
                    self.add_error("password1", exc)
        return cleaned


class AccountSettingsForm(forms.ModelForm):
    class Meta:
        model = Account
        fields = ["name", "timezone"]
        labels = {"name": "Organization / portfolio name", "timezone": "Timezone"}
        help_texts = {"timezone": "Decides when a day starts for due dates, overdue status, reminders and the calendar. "
                                  "Event times are read as local time in this zone."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = timezone_choices()
        current = self.instance.timezone
        if current not in dict(choices):              # keep a legacy/odd stored value selectable
            choices = [(current, current)] + choices
        self.fields["timezone"] = forms.ChoiceField(choices=choices, label="Timezone", help_text=self.Meta.help_texts["timezone"])
