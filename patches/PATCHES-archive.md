# Archive page: patches to existing files

New files are in `archive-app.zip` (unzip into the project root): the `archive` app and `ARCHIVE.md`. There are no database changes
(no migration). Below is every change to a file that already existed, as the changed function or snippet only. When done run
`python manage.py check_setup`; it should end with `All 71 checks passed.`

## 1. `config/settings.py`
Add to `INSTALLED_APPS`, after `"exports"`:
```python
    "archive",
```

## 2. `config/urls.py`
```python
    path("archive/", include("archive.urls")),
```

## 3. `core/crud.py`
Change the two imports near the top to include `NoReverseMatch` and `format_html`:
```python
from django.urls import NoReverseMatch, path, reverse
from django.utils.html import format_html
```
Replace the whole `TenantArchiveView` class (the confirmation message now has an **Undo** link):
```python
class TenantArchiveView(TenantViewMixin, View):
    requires_write = True
    model = None

    def post(self, request, pk):
        obj = get_object_or_404(self.model.objects.active(), pk=pk)
        obj.archive()
        messages.success(request, self.archived_message(obj))
        return redirect(self.url("list"))

    @staticmethod
    def archived_message(obj):
        """'Archived X.' plus an Undo link to the Archive page (plain text if that app isn't installed)."""
        try:
            from archive.registry import detail_url        # imported here so core never depends on archive at import time
            link = detail_url(obj)
        except (ImportError, NoReverseMatch):
            link = None
        return format_html('Archived {}. <a href="{}">Undo</a>', obj, link) if link else f"Archived {obj}."
```

## 4. `templates/base.html`
Replace the line that holds the Activity and Export links (the line starting `{% if request.membership.can_write %}`) with:
```html
    {% if request.membership.can_write %}<a href="{% url 'activity_list' %}">Activity</a><a href="{% url 'export_hub' %}">Export</a><a href="{% url 'archive_list' %}">Archive</a>{% endif %}
```

## 5. `attachments/templatetags/attachment_tags.py`
Replace `attachments_panel` (the only change: no upload form on archived records):
```python
@register.inclusion_tag("attachments/_panel.html", takes_context=True)
def attachments_panel(context, obj):
    """A 'Files' box for a record's edit page: list + (for writers) an upload form."""
    request = context.get("request")
    kind = kind_for(obj) if obj is not None else None
    if kind is None or not getattr(obj, "pk", None) or request is None or request.membership is None:
        return {"show": False}
    field = TARGETS[kind][1]
    items = list(Attachment.objects.filter(**{field: obj}).select_related("uploaded_by"))
    return {"show": True, "items": items, "kind": kind, "obj": obj, "request": request,
            "can_write": request.membership.can_write and getattr(obj, "archived_at", None) is None,   # no uploads to archived records
            "csrf_token": context.get("csrf_token"),
            "accept": ",".join("." + e for e in ALLOWED_EXTENSIONS), "limit": human_size(max_bytes())}
```

## 6. One existing test (`maintenance/tests/test_views.py`)
In `test_open_occurrence_page_renders_for_owner_with_actions` the last assertion was `self.assertNotContains(r, "Archive")`, which the new
menu item would trip. Change it to what it meant (no archive *button*):
```python
        self.assertNotContains(r, ">Archive</button>")               # occurrences are completed or skipped, never archived
```

## 7. `core/management/commands/check_setup.py` (optional: teach the checker about this feature)
* In the `INSTALLED_APPS` loop tuple add `"archive"` after `"exports"`.
* In the URL-name tuple add `"archive_list"` after `"export_hub"`.
* In the `base.html nav links` check add `"archive_list"` after `"export_hub"`.
* Add these two checks just before the `TenantFormMixin.archivable` check:
```python
    add("TenantArchiveView offers an Undo link", lambda: hasattr(mod("core.crud").TenantArchiveView, "archived_message"), "Archive patch, section 3: replace TenantArchiveView.post and add archived_message.")
    add("attachments panel hides uploads on archived records", lambda: source_has(mod("attachments.templatetags.attachment_tags").attachments_panel, "archived_at"), "Archive patch, section 5: replace attachments_panel in attachments/templatetags/attachment_tags.py.")
```
