# Read-only record pages: patches to existing files

New files are in `readonly-pages.zip` (unzip into the project root): the `details` app and `DETAILS.md`. No database changes.
Edit URLs do not change. Below is every change to an existing file, as the changed function or snippet only, in this order:
**(A) application code, sections 1-11**, **(B) the tests that encoded the old behaviour, section 12**, **(C) the setup checker, section 13**.
When done run `python manage.py check_setup`; it should end with `All 85 checks passed.`

## 1. `config/settings.py`
Add to `INSTALLED_APPS`, after `"archive"`:
```python
    "details",
```

## 2. `config/urls.py`
Add this line directly above `path("", include("core.urls")),`:
```python
    path("", include("details.urls")),
```

## 3. `core/crud.py`
`TenantListView`: replace `get_context_data` and add `row_url_name` right after it:
```python
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
            link_rows=self.request.membership.can_write or self.viewers_can_open or self.row_url_name()[1],
            export_url=("?" + (params.urlencode() + "&" if params else "") + "export=csv")
            if self.export_key and self.request.membership.can_write else None,
            edit_url_name=self.row_url_name()[0],
        )
        return ctx

    def row_url_name(self):
        """(url name, is_detail): rows open the record's read-only page when it has one, else its edit/action page."""
        name = f"{self.url_basename}_detail"
        try:
            reverse(name, args=[0])
            return name, True
        except NoReverseMatch:
            return f"{self.url_basename}_edit", False
```
`TenantFormMixin`: add `cancel_url` and replace `get_context_data`:
```python
    def cancel_url(self):
        """Back to the record's read-only page when editing one, otherwise to the list."""
        obj = getattr(self, "object", None)
        if obj is not None and obj.pk:
            try:
                return reverse(f"{self.url_basename}_detail", args=[obj.pk])
            except NoReverseMatch:
                pass
        return self.url("list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = self.title
        ctx["list_url"] = self.url("list")
        ctx["cancel_url"] = self.cancel_url()
        if self.archivable and getattr(self, "object", None) and self.object.pk:
            ctx["archive_url"] = self.url("archive", self.object.pk)
        return ctx
```
Replace the whole `TenantUpdateView` class:
```python
class TenantUpdateView(TenantFormMixin, UpdateView):
    def get_queryset(self):
        return self.model.objects.active()   # tenant-scoped: other accounts' pks 404

    def get_success_url(self):
        return self.cancel_url()             # after saving, show the record again
```

## 4. `templates/crud/form.html`
Three edits: delete the line `{% load attachment_tags audit_tags %}`; change the Cancel link to
```html
  <a class="btn secondary" href="{{ cancel_url|default:list_url }}">Cancel</a>
```
and delete the two lines `{% attachments_panel object %}` and `{% history_panel object %}` near the bottom (the panels now live on the read-only page).

## 5. `planner/services.py` (`calendar_entries`)
Two lines change (viewers can now open these pages, so there is no `can_write` condition; the `can_write` argument stays and is simply unused):
```python
reverse("event_detail", args=[ev.pk]),
reverse("inventoryitem_detail", args=[it.pk]), status, detail, "Warranty"))
```
The first replaces `reverse("event_edit", args=[ev.pk]) if can_write else None,`; the second replaces the `reverse("inventoryitem_edit", ...) if can_write else None` part of the Warranty entry.

## 6. `planner/templates/planner/dashboard.html`
Four links lose their `can_write` condition. Replace each occurrence of
`{% if request.membership.can_write %}<a href="{% url 'inventoryitem_edit' i.pk %}">{{ i.name }}</a>{% else %}{{ i.name }}{% endif %}` (3 times) with
```html
<a href="{% url 'inventoryitem_detail' i.pk %}">{{ i.name }}</a>
```
and the one occurrence of `{% if request.membership.can_write %}<a href="{% url 'event_edit' e.pk %}">{{ e.title }}</a>{% else %}{{ e.title }}{% endif %}` with
```html
<a href="{% url 'event_detail' e.pk %}">{{ e.title }}</a>
```

## 7. `audit/views.py`
Replace the `JUMP` dictionary:
```python
JUMP = {"property": "property_detail", "unit": "unit_detail", "contact": "contact_detail", "inventoryitem": "inventoryitem_detail",
        "maintenancetask": "task_detail", "taskoccurrence": "occurrence_edit", "event": "event_detail"}
```

## 8. Files: `attachments/targets.py` and `attachments/views.py`
In `targets.py` replace `EDIT_URL_NAME`:
```python
EDIT_URL_NAME = {"property": "property_detail", "item": "inventoryitem_detail", "task": "task_detail",   # where "the record" is: its read-only page
                 "occurrence": "occurrence_edit", "contact": "contact_detail", "event": "event_detail"}
```
In `views.py` replace `AttachmentDetail.get_context_data` (every role can open a record's read-only page, so the "open record" link is always shown):
```python
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        att = get_object_or_404(Attachment.objects.select_related("uploaded_by", "occurrence__task"), pk=self.kwargs["pk"])
        can_write = self.request.membership.can_write
        ctx.update(a=att, can_write=can_write,
                   target_url=_target_page(att))                      # every role can open a record's read-only page
        return ctx
```

## 9. Archive: `archive/deletion.py` and `archive/views.py`
In `deletion.py` replace `LIVE_EDIT`; in `views.py` replace the `edit_names` attribute of `ArchivedDetail` with the same mapping:
```python
LIVE_EDIT = {"property": "property_detail", "unit": "unit_detail", "item": "inventoryitem_detail", "contact": "contact_detail",
             "task": "task_detail", "event": "event_detail"}          # a live record's read-only page
```
(in `views.py` the attribute is called `edit_names` rather than `LIVE_EDIT`; same four-line value.)

## 10. `archive/services.py` (friendlier values, shared with the Archive page)
Replace the imports at the top of the file (the block before `from core.models import TenantModel`) with:
```python
import re
from datetime import date, datetime, time
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils.text import capfirst
```
then replace `describe_fields` with these three functions:
```python
def readable_label(f):
    """'Address line1' -> 'Address line 1'."""
    return re.sub(r"(?<=[a-z])(\d)$", r" \1", capfirst(str(f.verbose_name)))


def readable_value(obj, f, value):
    if value is None or value == "":
        return ""
    if f.choices:
        return dict(f.flatchoices).get(value, str(value))
    if f.many_to_one:
        return str(getattr(obj, f.name))
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, datetime):
        return f"{value:%b %-d, %Y %-I:%M %p}"
    if isinstance(value, date):
        return f"{value:%b %-d, %Y}"
    if isinstance(value, time):
        return f"{value:%-I:%M %p}"
    if isinstance(value, Decimal) and f.get_internal_type() == "DecimalField":
        return f"${value:,.2f}"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)


def describe_fields(obj):
    """[(label, value)] for a read-only view: choice labels, related names, friendly dates and money, no internal columns."""
    kind = kind_of(obj)
    skip = HIDDEN_FIELDS | (kind.skip_fields if kind else frozenset())
    rows = [(readable_label(f), readable_value(obj, f, getattr(obj, f.attname)))
            for f in obj._meta.concrete_fields if f.name not in skip]
    if kind:
        rows += kind.extra(obj)
    return rows
```

## 11. `maintenance/templates/maintenance/occurrence_form.html`
Change the "Edit task" link to the task's read-only page:
```html
<a class="btn secondary" href="{% url 'task_detail' task.pk %}">Task details</a>
```

## 12. Existing tests that encoded the old behaviour
These all asserted that the edit page was "the record's page". Each now asserts the new rule (links and redirects go to `/view/`; viewers get links to read-only pages and
still never to edit pages; the panels live on the read-only page). Single-line changes are shown as old → new; rewritten tests are given in full.

**One-line changes**

| File | Test | Old | New |
|---|---|---|---|
| `attachments/tests/test_views.py` | `UploadTests.test_owner_uploads_and_lands_back_on_the_record_with_a_thumbnail` | `f"/properties/{self.maple.pk}/")` in the redirect_chain assertion | `f"/properties/{self.maple.pk}/view/")` |

| `attachments/tests/test_views.py` | `UploadTests.test_next_parameter_cannot_redirect_off_site` | `self.assertEqual(r["Location"], f"/properties/{self.maple.pk}/", bad)` | `... f"/properties/{self.maple.pk}/view/", bad)` |

| `attachments/tests/test_views.py` | `DeleteTests.test_writer_deletes_row_and_file_and_returns_to_the_record` | `assertRedirects(r, f"/properties/{self.maple.pk}/", ...` | `assertRedirects(r, f"/properties/{self.maple.pk}/view/", ...` |

| `audit/tests/test_feed.py` | `JumpTests.test_jump_opens_the_record_if_it_still_exists` | `f"/properties/{self.birch.pk}/", fetch_redirect_response=False)` | `f"/properties/{self.birch.pk}/view/", fetch_redirect_response=False)` |

| `audit/tests/test_feed.py` | `HistoryPanelTests.test_record_page_shows_its_own_recent_history_with_a_link_to_all_of_it` | `r = self.client.get(f"/properties/{self.birch.pk}/")` | `r = self.client.get(f"/properties/{self.birch.pk}/view/")` |

| `audit/tests/test_feed.py` | `HistoryPanelTests.test_panel_shows_only_the_latest_entries` | `body = self.client.get(f"/properties/{self.birch.pk}/").content.decode()` | `body = self.client.get(f"/properties/{self.birch.pk}/view/").content.decode()` |

| `maintenance/tests/test_views.py` | `OccurrencePageTests.test_open_occurrence_page_renders_for_owner_with_actions` | `"Edit task"` in the needles tuple | `"Task details"` |

| `archive/tests/test_archive.py` | `DetailTests.test_a_record_that_is_active_again_redirects_to_its_live_page` | the `live = {...}` URLs without `/view/` | add `/view/` to every URL (e.g. `"/properties/{}/view/"`, `"/properties/units/{}/view/"`) |

| `archive/tests/test_archive.py` | `RestoreTests.test_files_survive_an_archive_and_restore` | `self.client.get(f"/properties/{self.objs['property'].pk}/")` | `self.client.get(f"/properties/{self.objs['property'].pk}/view/")` |

| `archive/tests/test_delete.py` | `PlanTests.test_property_is_blocked_by_everything_that_belongs_to_it` | `f"/properties/units/{objs['unit'].pk}/")` (the *active* case) | `f"/properties/units/{objs['unit'].pk}/view/")` |

| `archive/tests/test_delete_views.py` | `ConfirmPageTests.test_a_blocked_record_shows_what_is_in_the_way_and_offers_no_delete_form` | `f'href="/properties/units/{objs["unit"].pk}/"'` | `f'href="/properties/units/{objs["unit"].pk}/view/"'` |

| `archive/tests/test_delete_views.py` | `ConfirmPageTests.test_active_records_are_sent_back_to_their_live_page_with_an_explanation` | the `live = {...}` URLs without `/view/` | add `/view/` to every URL |

**Rewritten tests (replace the whole function; where the name changed, delete the old one)**
```python
# core/tests/test_views.py, class RowLinkTests: replaces test_viewers_are_not_offered_links_to_pages_that_403
    def test_rows_open_the_read_only_page_for_everyone_and_edit_pages_stay_closed_to_viewers(self):
        cases = (("/properties/", self.maple), ("/contacts/", self.plumber), ("/inventory/", self.heater))
        for user in (self.alice, self.vic):
            self.login(user)
            for list_url, obj in cases:
                detail, edit = f"{list_url}{obj.pk}/view/", f"{list_url}{obj.pk}/"
                r = self.client.get(list_url)
                self.assertContains(r, f'href="{detail}"'); self.assertNotContains(r, f'href="{edit}"')     # rows never jump straight to edit
                self.assertEqual(self.client.get(detail).status_code, 200, (user.email, detail))
        self.login(self.vic)
        for list_url, obj in cases:
            self.assertEqual(self.client.get(f"{list_url}{obj.pk}/").status_code, 403)                     # the edit page really is forbidden to them
        self.login(self.alice)
        self.assertEqual(self.client.get(f"/contacts/{self.plumber.pk}/").status_code, 200)


# planner/tests/test_feed.py, class FeedTests: replaces test_links_only_where_the_user_can_open_the_page
    def test_every_entry_links_to_a_page_every_role_can_open_never_to_an_edit_page(self):
        ev = self.event(self.acme, "e", D(2026, 11, 12)); occ = self.occ(self.acme, self.maple, "t", D(2026, 11, 12))
        it = self.item(self.acme, self.maple, "i", warranty_expires_on=D(2026, 11, 12))
        viewer = {e.kind: e.url for e in self.feed(D(2026, 11, 1), D(2026, 11, 30), can_write=False)}
        writer = {e.kind: e.url for e in self.feed(D(2026, 11, 1), D(2026, 11, 30), can_write=True)}
        self.assertEqual(viewer, writer)
        self.assertEqual(writer, {"event": f"/planner/events/{ev.pk}/view/", "warranty": f"/inventory/{it.pk}/view/",
                                  "task": f"/maintenance/{occ.pk}/"})


# planner/tests/test_views.py, class DashboardPageTests
    def test_viewer_only_gets_links_to_pages_they_can_open(self):
        self.seed()
        self.login(self.vic)
        body = self.client.get("/").content.decode()
        self.assertIn("Samsung Dishwasher", body); self.assertIn("City inspection", body)
        self.assertRegex(body, r'href="/inventory/\d+/view/"'); self.assertRegex(body, r'href="/planner/events/\d+/view/"')   # read-only pages
        self.assertNotRegex(body, r'href="/inventory/\d+/"'); self.assertNotRegex(body, r'href="/planner/events/\d+/"')        # never an edit page
        self.assertRegex(body, r'href="/maintenance/\d+/"')                                                                     # job pages are viewable


# planner/tests/test_views.py, class CalendarPageTests
    def test_viewer_sees_chips_but_only_linkable_ones_are_links(self):
        ev = self.event(self.acme, "inspection", days(1)); occ = self.occ(self.acme, self.maple, "pump", days(1))
        self.item(self.acme, self.maple, "boiler", warranty_expires_on=days(1))
        self.login(self.vic)
        body = self.client.get("/planner/").content.decode()
        for t in ("inspection", "pump", "boiler"):
            self.assertIn(t, body)
        self.assertIn(f'href="/planner/events/{ev.pk}/view/"', body); self.assertNotIn(f'href="/planner/events/{ev.pk}/"', body)
        self.assertRegex(body, r'href="/inventory/\d+/view/"'); self.assertNotRegex(body, r'href="/inventory/\d+/"')
        self.assertIn(f'href="/maintenance/{occ.pk}/"', body)


# attachments/tests/test_views.py, class PagesTests: replaces test_viewer_sees_the_page_without_delete_and_without_links_they_cannot_open
    def test_viewer_sees_the_page_without_delete_and_with_links_only_to_pages_they_can_open(self):
        on_property = self.store(self.maple, "a.png", png())
        occ = self.occ(self.acme, self.maple, "Pump", self.TODAY); on_occ = self.store(occ, "b.png", png())
        self.login(self.vic)
        r = self.client.get(f"/files/{on_property.pk}/")
        self.assertNotContains(r, "Delete"); self.assertContains(r, f'href="/properties/{self.maple.pk}/view/"')       # the read-only page, not the edit page
        self.assertNotContains(r, f'href="/properties/{self.maple.pk}/"')
        r = self.client.get(f"/files/{on_occ.pk}/")
        self.assertContains(r, f'href="/maintenance/{occ.pk}/"'); self.assertNotContains(r, "Delete")


# attachments/tests/test_views.py, class PanelTests: replaces test_panel_appears_on_every_record_page_for_writers and adds two tests
    def test_panel_appears_on_every_record_page_for_writers(self):
        occ = self.occ(self.acme, self.maple, "Pump", self.TODAY); ev = self.event(self.acme, "Inspection", self.TODAY)
        self.login(self.alice)
        pages = ((f"/properties/{self.maple.pk}/view/", "property", self.maple.pk), (f"/inventory/{self.heater.pk}/view/", "item", self.heater.pk),
                 (f"/contacts/{self.plumber.pk}/view/", "contact", self.plumber.pk), (f"/planner/events/{ev.pk}/view/", "event", ev.pk),
                 (f"/maintenance/tasks/{occ.task.pk}/view/", "task", occ.task.pk), (f"/maintenance/{occ.pk}/", "occurrence", occ.pk))
        for url, kind, pk in pages:
            r = self.client.get(url)
            self.assertContains(r, "<h2>Files</h2>", msg_prefix=url); self.assertContains(r, f'action="/files/upload/{kind}/{pk}/"', msg_prefix=url)
            self.assertContains(r, 'accept=".csv,.docx')

    def test_edit_pages_are_just_the_form_without_the_files_panel(self):
        self.login(self.alice)
        for url in (f"/properties/{self.maple.pk}/", f"/contacts/{self.plumber.pk}/", f"/inventory/{self.heater.pk}/"):
            self.assertNotContains(self.client.get(url), "<h2>Files</h2>", msg_prefix=url)

    def test_viewers_can_see_files_on_a_records_read_only_page_but_not_upload(self):
        self.store(self.maple, "deed.png", png()); self.login(self.vic)
        r = self.client.get(f"/properties/{self.maple.pk}/view/")
        self.assertContains(r, "deed.png"); self.assertNotContains(r, 'type="file"')


# audit/tests/test_feed.py, class HistoryPanelTests: new test
    def test_the_history_panel_lives_on_the_read_only_page_not_the_edit_form(self):
        self.login(self.alice)
        self.assertContains(self.client.get(f"/properties/{self.birch.pk}/view/"), "<h2>History")
        self.assertNotContains(self.client.get(f"/properties/{self.birch.pk}/"), "<h2>History")
```

## 13. `core/management/commands/check_setup.py` (optional, recommended)
* In the `INSTALLED_APPS` loop tuple add `"details"` after `"archive"`.
* In the URL-name tuple add `"property_detail", "unit_detail", "inventoryitem_detail", "contact_detail", "task_detail", "event_detail"` after `"archive_list"`.
* Replace the inner `resolves` function (detail routes need an id):
```python
        def resolves(n=name):
            for args in ([], [1]):                      # most routes take no argument; detail routes take an id
                try:
                    reverse(n, args=args)
                    return True
                except NoReverseMatch:
                    continue
            return False
```
* Replace the check named `crud/form.html panels` with these three:
```python
    add("crud/form.html Cancel returns to the record", t("crud/form.html", "cancel_url"), "Read-only pages patch, section 4: use `cancel_url|default:list_url` for the Cancel link in templates/crud/form.html (and remove the Files/History panels from it).")
    add("record page has the Files and History panels", t("details/detail.html", "attachments_panel", "history_panel"), "Unzip the details app (details/templates/details/detail.html).")
    add("dashboard links to read-only item pages", t("planner/dashboard.html", "inventoryitem_detail", "event_detail"), "Read-only pages patch, section 6: link events and inventory items on the dashboard to their *_detail pages.")
```
* Add these two checks just before the `TenantFormMixin.archivable` check:
```python
    add("list rows open read-only pages", lambda: hasattr(L(), "row_url_name"), "Read-only pages patch, section 3: add row_url_name and use it in TenantListView.get_context_data.")
    add("editing returns to the record", lambda: hasattr(F(), "cancel_url") and source_has(mod("core.crud").TenantUpdateView.get_success_url, "cancel_url"), "Read-only pages patch, section 3: add TenantFormMixin.cancel_url and TenantUpdateView.get_success_url.")
```
