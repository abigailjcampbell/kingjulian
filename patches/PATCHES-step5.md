# Step 5 patches to existing files

New files are in `attachments-audit-exports.zip` (unzip into the project root). It adds the apps `attachments`, `audit` and `exports`
(with migrations, templates, tests, the `purge_orphan_attachments` command) and `STEP5.md`. Below is every change to a file that
already existed, as the changed function or snippet only. Then:
`pip install -r requirements-dev.txt && python manage.py migrate`.

## 1. `config/settings.py`
Add to `INSTALLED_APPS` (after `"planner"`):
```python
    "attachments",
    "audit.apps.AuditConfig",
    "exports",
```
Add to `MIDDLEWARE`: the first line directly after `SecurityMiddleware` (it must run before `CsrfViewMiddleware`), the second after `ActiveAccountMiddleware`:
```python
    "attachments.middleware.UploadSizeMiddleware",     # must precede CsrfViewMiddleware
    ...
    "audit.middleware.ActorMiddleware",                 # who is acting, for the activity log
```
Append:
```python
# Uploaded files live outside any web-served path; they are only ever sent by the authenticated download views.
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "media"))
ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024            # per file
ATTACHMENT_ACCOUNT_QUOTA_BYTES = 500 * 1024 * 1024  # per account
ATTACHMENT_MAX_PER_TARGET = 50                      # files per property/item/task/...
FILE_UPLOAD_PERMISSIONS = 0o640                    # stored files: owner read/write, group read, nobody else
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o750
```

## 2. `config/urls.py`
```python
    path("files/", include("attachments.urls")),
    path("activity/", include("audit.urls")),
    path("exports/", include("exports.urls")),
```

## 3. `core/crud.py` (`TenantListView`)
Add the class attribute next to `viewers_can_open`:
```python
    export_key = None                # exports.datasets key; enables ?export=csv for owners and managers
```
Add these two methods (before `get_template_names`) and replace `get_context_data`:
```python
    def get(self, request, *args, **kwargs):
        if request.GET.get("export") == "csv" and self.export_key:
            return self.export_csv()
        return super().get(request, *args, **kwargs)

    def export_csv(self):
        """Download exactly what the list is showing (filters and search applied, no pagination)."""
        if not self.request.membership.can_write:
            raise PermissionDenied("Exporting needs an owner or manager role.")
        from exports.responses import csv_response      # imported here so core never depends on exports at import time
        return csv_response(self.export_key, self.get_queryset(), self.request.account)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        params = self.request.GET.copy()
        params.pop("page", None)
        ctx.update(
            title=self.title,
            columns=self.columns,
            q=self.request.GET.get("q", ""),
            querystring=params.urlencode(),
            filter_fields=[
                {
                    "param": f.param,
                    "label": f.label,
                    "choices": f.choices(),
                    "selected": self.request.GET.get(f.param, f.default),
                }
                for f in self.filters
            ],
            create_url=self.url("create") if self.has_create else None,
            link_rows=self.request.membership.can_write or self.viewers_can_open,
            export_url=("?" + (params.urlencode() + "&" if params else "") + "export=csv")
            if self.export_key and self.request.membership.can_write else None,
            edit_url_name=f"{self.url_basename}_edit",
        )
        return ctx
```

## 4. `export_key` on the existing list views (one line each, under `title = ...`)
| File | Class | Line to add |
|---|---|---|
| `properties/views.py` | `PropertyList` | `export_key = "properties"` |
| `properties/views.py` | `UnitList` | `export_key = "units"` |
| `contacts/views.py` | `ContactList` | `export_key = "contacts"` |
| `inventory/views.py` | `InventoryList` | `export_key = "inventory"` |
| `maintenance/views.py` | `TaskList` | `export_key = "tasks"` |
| `maintenance/views.py` | `OccurrenceList` | `export_key = "history"` |
| `planner/views.py` | `EventList` | `export_key = "events"` |

## 5. Templates
* `templates/base.html`, after the Calendar link in the nav:
```html
    <a href="{% url 'attachment_list' %}">Files</a>
    {% if request.membership.can_write %}<a href="{% url 'activity_list' %}">Activity</a><a href="{% url 'export_hub' %}">Export</a>{% endif %}
```
* `templates/crud/list.html`, before the "+ New" link (and remove `style="margin-left:auto"` from "+ New"; the Export button carries it):
```html
  {% if export_url %}<a class="btn secondary" href="{{ export_url }}" style="margin-left:auto">Export CSV</a>{% endif %}
```
* `templates/crud/form.html`: add `{% load attachment_tags audit_tags %}` on the line after `{% extends "base.html" %}`, and these two lines just before the final `{% endblock %}`:
```html
{% attachments_panel object %}
{% history_panel object %}
```
* `maintenance/templates/maintenance/occurrence_form.html`: the same `{% load ... %}` line after `extends`, and the same two lines just before the final `{% endblock %}`.

## 6. `requirements-dev.txt`
The tests build real files with these libraries:
```
Pillow
reportlab
python-docx
openpyxl
```
