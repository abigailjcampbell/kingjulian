# Account timezone: patches to existing files

New files are in `timezone-new-files.zip` (unzip into the project root). It adds `accounts/timezones.py`, the migration
`accounts/migrations/0002_account_timezone.py`, `templates/accounts/settings.html`, `accounts/TIMEZONES.md`, and three test files.
Below is every change to a file that already existed, as the changed function or class only. Then run
`pip install -r requirements.txt && python manage.py migrate` (existing accounts become `UTC`).

## 1. `requirements.txt`
```
tzdata   # IANA timezone data (needed on Windows / slim images)
```

## 2. `accounts/models.py`
Add to the imports: `from django.utils import timezone as dj_timezone` and
`from .timezones import DEFAULT as DEFAULT_TIMEZONE, load_zone, validate_timezone`. Then replace `Account`:
```python
class Account(models.Model):
    """The tenant. Every tenant-owned row points at exactly one Account."""

    name = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)
    # What "today" means for due dates, overdue status, reminders and the calendar (IANA name).
    timezone = models.CharField(max_length=64, default=DEFAULT_TIMEZONE, validators=[validate_timezone])

    def __str__(self):
        return self.name

    @property
    def zone(self):
        """tzinfo for this account; falls back to UTC if the stored name is unusable."""
        return load_zone(self.timezone)

    def today(self):
        """The account's current calendar date, right now."""
        return dj_timezone.localdate(timezone=self.zone)
```

## 3. `accounts/forms.py`
Add to the imports: `from .models import Account` and `from .timezones import DEFAULT, timezone_choices`. Replace `SignupForm` (its
`clean_email` and `clean` are unchanged; `__init__` and `clean_timezone` plus the `timezone` field are new) and add `AccountSettingsForm`:
```python
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
```

## 4. `accounts/views.py`
Add to the imports: `from django.contrib import messages`, `from django.core.exceptions import PermissionDenied`,
`from django.urls import reverse_lazy`, `from django.utils import timezone`, `from django.views.generic import FormView, UpdateView`,
`from core.crud import TenantViewMixin`, and `from .forms import AccountSettingsForm, SignupForm`. Replace `SignupView.form_valid` and add the new view:
```python
    def form_valid(self, form):
        with transaction.atomic():
            user = User.objects.create_user(form.cleaned_data["email"], form.cleaned_data["password1"])
            account = Account.objects.create(name=form.cleaned_data["account_name"],
                                             timezone=form.cleaned_data["timezone"])
            Membership.objects.create(user=user, account=account, role=Membership.Role.OWNER)
        login(self.request, user)
        self.request.session["account_id"] = account.pk
        return redirect("home")


class AccountSettingsView(TenantViewMixin, UpdateView):
    """Owner-only: rename the account and set its timezone."""

    form_class = AccountSettingsForm
    template_name = "accounts/settings.html"
    success_url = reverse_lazy("account_settings")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.membership and request.membership.role != Membership.Role.OWNER:
            raise PermissionDenied("Only owners can change account settings.")
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        return self.request.account                  # always the *active* account, never one named in the URL

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["local_now"] = timezone.localtime()      # rendered in the account's current timezone
        return ctx

    def form_valid(self, form):
        messages.success(self.request, "Account settings saved.")
        return super().form_valid(form)
```

## 5. `accounts/urls.py`
```python
    path("settings/", views.AccountSettingsView.as_view(), name="account_settings"),
```

## 6. `core/middleware.py`
Add `from django.utils import timezone` to the imports. Replace `ActiveAccountMiddleware.__call__`:
```python
    def __call__(self, request):
        from accounts.models import Membership  # avoid import at settings load

        request.account = None
        request.membership = None
        request.memberships = []
        token = None
        zone_activated = False

        if request.user.is_authenticated:
            memberships = list(
                Membership.objects.select_related("account").filter(user=request.user).order_by("id")
            )
            request.memberships = memberships
            wanted = request.session.get("account_id")
            chosen = next((m for m in memberships if m.account_id == wanted), None)
            if chosen is None and memberships:
                chosen = memberships[0]
            if chosen is not None:
                request.membership = chosen
                request.account = chosen.account
                request.session["account_id"] = chosen.account_id
                token = set_account(chosen.account_id)
                # Everything that asks timezone.localdate()/localtime() now sees the account's own clock.
                timezone.activate(chosen.account.zone)
                zone_activated = True
        try:
            return self.get_response(request)
        finally:
            if token is not None:
                reset_account(token)
            if zone_activated:
                timezone.deactivate()
```

## 7. `maintenance/management/commands/send_maintenance_reminders.py`
Remove the now-unused `from django.utils import timezone`. Replace both methods of `Command`:
```python
    def add_arguments(self, parser):
        parser.add_argument("--today", type=date.fromisoformat,
                            help="Pretend today is YYYY-MM-DD for every account (testing). Default: each account's own local date.")
        parser.add_argument("--dry-run", action="store_true", help="Report what would be sent; change nothing.")

    def handle(self, *args, today=None, dry_run=False, **opts):
        total = Counter()
        accounts = 0
        with transaction.atomic():
            for account in Account.objects.order_by("id"):
                day = today or account.today()           # "today" depends on where the account is
                with use_account(account):
                    gen = reminders.generate_reminders(day)
                    sent = reminders.send_pending(day, dry_run=dry_run)
                total.update(gen); total.update(sent); accounts += 1
                self.stdout.write(f"{account.name} [{account.timezone}, {day}]: {dict(gen) | dict(sent) or 'nothing to do'}")
            if dry_run:
                transaction.set_rollback(True)     # generation writes rows; discard them
        label = "DRY RUN " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(f"{label}{accounts} account(s): {dict(total) or 'nothing to do'}"))
```

## 8. `planner/ical.py`
Change the imports to `from datetime import datetime, timedelta, timezone as dt_timezone`, add `UTC = dt_timezone.utc` above `PRODID`,
and update the module docstring ("timed events are converted from the account's local time to UTC"). Add `_utc`, replace `_event_lines` and `build_ics`:
```python
def _utc(day, clock, zone) -> datetime:
    """Local wall-clock time in `zone` -> UTC. Nonexistent (spring-forward) and ambiguous (fall-back)
    times resolve the way zoneinfo does (fold=0), so they never raise."""
    return datetime.combine(day, clock, tzinfo=zone).astimezone(UTC)


def _event_lines(e: Entry, account_id: int, stamp: datetime, zone) -> list[str]:
    lines = ["BEGIN:VEVENT", f"UID:{e.uid}@a{account_id}.{UID_DOMAIN}", f"DTSTAMP:{stamp:%Y%m%dT%H%M%SZ}"]
    if e.start_time:
        begin = _utc(e.start, e.start_time, zone)
        has_end = e.end_time and (e.end > e.start or e.end_time > e.start_time)
        finish = _utc(e.end, e.end_time, zone) if has_end else begin + timedelta(hours=1)
        if finish <= begin:          # e.g. 02:30-03:30 across a spring-forward gap collapses to zero length
            finish = begin + timedelta(hours=1)
        lines += [f"DTSTART:{begin:%Y%m%dT%H%M%SZ}", f"DTEND:{finish:%Y%m%dT%H%M%SZ}"]
    else:
        lines += [f"DTSTART;VALUE=DATE:{e.start:%Y%m%d}", f"DTEND;VALUE=DATE:{e.end + timedelta(days=1):%Y%m%d}"]
        lines.append("TRANSP:TRANSPARENT")                                               # don't block free/busy
    title = e.title + (" (done)" if e.status == "done" else " (overdue)" if e.status == "overdue" else "")
    lines.append(f"SUMMARY:{escape(title)}")
    description = "\n".join(filter(None, [e.where, e.detail]))
    if description:
        lines.append(f"DESCRIPTION:{escape(description)}")
    if e.where:
        lines.append(f"LOCATION:{escape(e.where)}")
    lines += [f"CATEGORIES:{escape(e.label)}", "END:VEVENT"]
    return lines


def build_ics(entries: list[Entry], *, account_name: str, account_id: int, now: datetime, zone=UTC) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             f"X-WR-CALNAME:{escape(account_name)}", f"X-WR-TIMEZONE:{getattr(zone, 'key', 'UTC')}"]
    for e in entries:
        lines += _event_lines(e, account_id, now, zone)
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"
```

## 9. `planner/views.py`
Replace `IcsView.get`:
```python
    def get(self, request):
        today = timezone.localdate()
        entries = calendar_entries(today - timedelta(days=ICS_DAYS_BACK), today + timedelta(days=ICS_DAYS_AHEAD),
                                   today=today, can_write=False)
        body = build_ics(entries, account_name=request.account.name, account_id=request.account.pk,
                         now=timezone.now(), zone=request.account.zone)
        response = HttpResponse(body, content_type="text/calendar; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="property-calendar.ics"'
        response["Cache-Control"] = "private, no-store"
        return response
```

## 10. Templates
* `templates/base.html`: after the line that shows `request.membership.get_role_display`, add
  `{% if request.membership.role == "owner" %}<a href="{% url 'account_settings' %}">Settings</a>{% endif %}`.
* `templates/accounts/signup.html`: before `{% endblock %}` add the timezone pre-selection script:
```html
<script>
  // Preselect the visitor's own timezone; the server still validates whatever is submitted.
  try {
    var tz = Intl.DateTimeFormat().resolvedOptions().timeZone, sel = document.getElementById("id_timezone");
    if (sel && tz && Array.prototype.some.call(sel.options, function (o) { return o.value === tz; })) sel.value = tz;
  } catch (e) {}
</script>
```

## 11. Existing tests that encoded the old "floating time" behaviour
In `planner/tests/test_ical.py` add `timezone as tz_` to the datetime import, add `UTC = tz_.utc` below `NOW`, and replace these three
tests in `DocumentTests` (they asserted naive local times; timed events are now UTC):
```python
    def test_timed_events_are_written_in_utc(self):
        _, cal = ics(ev(start_time=T(10, 0), end_time=T(12, 30)))          # default zone is UTC
        v = cal.walk("VEVENT")[0]
        self.assertEqual((v["DTSTART"].dt, v["DTEND"].dt), (datetime(2026, 11, 15, 10, tzinfo=UTC), datetime(2026, 11, 15, 12, 30, tzinfo=UTC)))
        self.assertNotIn("TRANSP", v)

    def test_missing_end_time_defaults_to_one_hour_even_across_midnight(self):
        _, cal = ics(ev(start_time=T(23, 30)))
        v = cal.walk("VEVENT")[0]
        self.assertEqual((v["DTSTART"].dt, v["DTEND"].dt), (datetime(2026, 11, 15, 23, 30, tzinfo=UTC), datetime(2026, 11, 16, 0, 30, tzinfo=UTC)))

    def test_multi_day_timed_event_uses_end_date_and_time(self):
        _, cal = ics(ev(start=D(2026, 11, 15), end=D(2026, 11, 17), start_time=T(9, 0), end_time=T(8, 0)))
        v = cal.walk("VEVENT")[0]
        self.assertEqual((v["DTSTART"].dt, v["DTEND"].dt), (datetime(2026, 11, 15, 9, tzinfo=UTC), datetime(2026, 11, 17, 8, tzinfo=UTC)))
```

## 12. Two stale lines in earlier READMEs
* `planner/README.md`, Known limits: delete the bullet "No account timezone yet..."; in ".ics notes" replace "Timed events are **floating local times** (no timezone)" with "Timed events are converted from the account's timezone to UTC".
* `maintenance/README.md`, Known limits: replace the bullet about "today" being the server's date with "s"
