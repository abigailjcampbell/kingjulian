# Permanent delete: patches to existing files

New files are in `permanent-delete.zip` (unzip into the project root): `archive/deletion.py`, `archive/delete_views.py`,
`archive/templates/archive/delete.html`, two test files, and `DELETE.md`. No database changes (no migration).
Below is every change to a file that already existed, as the changed function or snippet only. When done run
`python manage.py check_setup`; it should end with `All 74 checks passed.`

## 1. `archive/urls.py`
Add this import above the existing `from . import views as v`:
```python
from . import delete_views as dv
```
and add this path inside `urlpatterns`, after the `archive_restore` path:
```python
    path("<str:kind>/<int:pk>/delete/", dv.ArchiveDelete.as_view(), name="archive_delete"),
```

## 2. `archive/templates/archive/list.html`
In the last table cell, directly after the closing `</form>` of the Restore button, add this line:
```html
        {% if request.membership.role == "owner" %}<a href="{% url 'archive_delete' r.kind.key r.obj.pk %}" style="color:#b42318;font-size:.85rem">Delete…</a>{% endif %}
```

## 3. `archive/templates/archive/detail.html`
Directly after the line `{% if blockers %}<p><a class="btn secondary" href="{% url 'archive_list' %}">Back to archive</a></p>{% endif %}`, add:
```html
{% if request.membership.role == "owner" %}<p><a href="{% url 'archive_delete' kind.key obj.pk %}" style="color:#b42318">Delete permanently…</a></p>{% endif %}
```

## 4. `audit/context.py`
Append to the end of the file (the file already imports `contextlib` and `contextvars`):
```python
_quiet = contextvars.ContextVar("audit_quiet", default=False)


def is_quiet() -> bool:
    return _quiet.get()


@contextlib.contextmanager
def quiet():
    """Suppress the automatic per-row entries while a bulk operation runs; the caller then records one summary entry."""
    token = _quiet.set(True)
    try:
        yield
    finally:
        _quiet.reset(token)
```

## 5. `audit/signals.py`
Change the import line `from .context import get_actor` to:
```python
from .context import get_actor, is_quiet
```
and replace `record`:
```python
def record(*, account_id, action, summary, target=("", None, ""), changes=None):
    if is_quiet():                                   # inside audit.context.quiet(): the caller writes one summary entry
        return
    actor = get_actor()
    ActivityLog(account_id=account_id, actor=actor, actor_email=getattr(actor, "email", "") or "", action=action,
                target_type=target[0], target_id=target[1], target_label=target[2][:200],
                summary=summary[:400], changes=changes or {}).save()
```

## 6. `attachments/validators.py`
Replace `human_size` (it printed sizes like "1.44922 KB"; now at most one decimal, "1.4 KB"):
```python
def human_size(n: int) -> str:
    """'1.4 KB', '10 MB', '512 bytes': at most one decimal, no trailing zero."""
    def trim(x):
        return f"{x:.1f}".rstrip("0").rstrip(".")
    if n >= 1024 * 1024:
        return f"{trim(n / (1024 * 1024))} MB"
    return f"{trim(n / 1024)} KB" if n >= 1024 else f"{n} bytes"
```

## 7. `attachments/tests/test_validators.py` (optional but recommended)
Add this test next to `test_human_size`:
```python
    def test_human_size_never_prints_a_long_decimal(self):
        cases = {0: "0 bytes", 1023: "1023 bytes", 1024: "1 KB", 1484: "1.4 KB", 1536: "1.5 KB", 1126: "1.1 KB", 1048575: "1024 KB",
                 1048576: "1 MB", 1500000: "1.4 MB", 5 * 1024 * 1024 + 100000: "5.1 MB", 123456789: "117.7 MB"}
        for n, want in cases.items():
            self.assertEqual(human_size(n), want, n)
```

## 8. `core/management/commands/check_setup.py` (optional: teach the checker about this feature)
Add these three checks just before the `TenantFormMixin.archivable` check:
```python
    add("audit quiet mode exists and is honoured", lambda: hasattr(mod("audit.context"), "quiet") and source_has(mod("audit.signals").record, "is_quiet"), "Permanent-delete patch, sections 4 and 5: add quiet()/is_quiet() to audit/context.py and the early return to audit.signals.record.")
    add("human_size formats sizes to one decimal", lambda: mod("attachments.validators").human_size(1484) == "1.4 KB", "Permanent-delete patch, section 6: replace human_size in attachments/validators.py.")
    add("permanent delete is wired up", lambda: bool(mod("archive.delete_views")) and bool(mod("archive.deletion")) and bool(reverse("archive_delete", args=["contact", 1])), "Permanent-delete patch, section 1: add the archive_delete path (and the delete_views import) to archive/urls.py; unzip the new files.")
```
