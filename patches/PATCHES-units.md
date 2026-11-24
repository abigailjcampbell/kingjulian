# Units as a subsection of Properties: patches to existing files

New files are in `units-under-properties.zip` (unzip into the project root): `properties/tests/` (19 new tests). No database changes.
Units no longer have a tab, a dashboard card or a list of their own. They live on the property:

* the **property page** shows its units (always, with a **+ Add unit** button, for owners/managers; for viewers only when there are some), capped at 8;
* **See all N →** opens `/properties/<id>/units/`: that property's units with search, paging, Export CSV, and a "← <property>" link;
* **+ Add unit** opens the unit form with the property already chosen; saving returns to that property's unit list, **Cancel** returns to the property;
* a unit's own page says "Back to <property name>"; archiving a unit returns to its property (with the usual **Undo** link);
* the old `/properties/units/` address redirects to Properties, so bookmarks still work. Unit pages, edit pages, archive/restore/delete and the activity log are unchanged.

Below is every change to a file that already existed, as the changed function or snippet only. When done run `python manage.py check_setup`
(it should end with `All 92 checks passed.`) and `python manage.py test`.

## 1. Delete the Units tab
`templates/base.html`: delete the line
```html
    <a href="{% url 'unit_list' %}">Units</a>
```
`planner/services.py` (`dashboard`): delete the line `"Units": (Unit.objects.active().count(), "unit_list"),` from `d.counts`, and change the import to `from properties.models import Property`.
(Optional clean-up: `core/views.py` and `templates/home.html` have been unused since step 4 and still mention `unit_list`; delete both files.)

## 2. `core/crud.py`
Child records need to say where "back" is. `TenantListView`: add two class attributes under `paginate_by = 25`:
```python
    back_url = None                  # optional "← back" link above the list (child lists point at their parent)
    back_label = ""
```
and replace `get_context_data` (new: `create_url` and `back_url`/`back_label`), then add `get_create_url` before `row_url_name`:
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
            create_url=self.get_create_url() if self.has_create else None,
            back_url=self.back_url, back_label=self.back_label,
            link_rows=self.request.membership.can_write or self.viewers_can_open or self.row_url_name()[1],
            export_url=("?" + (params.urlencode() + "&" if params else "") + "export=csv")
            if self.export_key and self.request.membership.can_write else None,
            edit_url_name=self.row_url_name()[0],
        )
        return ctx

    def get_create_url(self):
        return self.url("create")

    def row_url_name(self):
        """(url name, is_detail): rows open the record's read-only page when it has one, else its edit/action page."""
        name = f"{self.url_basename}_detail"
        try:
            reverse(name, args=[0])
            return name, True
        except NoReverseMatch:
            return f"{self.url_basename}_edit", False
```
`TenantFormMixin`: add `get_list_url`, and replace `get_success_url`, `cancel_url` and `get_context_data` (they now use it instead of `self.url("list")`):
```python
    def get_list_url(self):
        """Where 'the list' is for this record type; child records (units) override this to point at their parent."""
        return self.url("list")

    def get_success_url(self):
        return self.get_list_url()

    def cancel_url(self):
        """Back to the record's read-only page when editing one, otherwise to the list."""
        obj = getattr(self, "object", None)
        if obj is not None and obj.pk:
            try:
                return reverse(f"{self.url_basename}_detail", args=[obj.pk])
            except NoReverseMatch:
                pass
        return self.get_list_url()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = self.title
        ctx["list_url"] = self.get_list_url()
        ctx["cancel_url"] = self.cancel_url()
        if self.archivable and getattr(self, "object", None) and self.object.pk:
            ctx["archive_url"] = self.url("archive", self.object.pk)
        return ctx
```
Replace `TenantArchiveView` (new `after_archive_url`):
```python
class TenantArchiveView(TenantViewMixin, View):
    requires_write = True
    model = None

    def post(self, request, pk):
        obj = get_object_or_404(self.model.objects.active(), pk=pk)
        obj.archive()
        messages.success(request, self.archived_message(obj))
        return redirect(self.after_archive_url(obj))

    def after_archive_url(self, obj):
        """Where to land after archiving; child records (units) override this to return to their parent."""
        return self.url("list")

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

## 3. `templates/crud/list.html`
Add this line directly above `<h1>{{ title }}</h1>`:
```html
{% if back_url %}<p style="margin:0 0 .4rem"><a href="{{ back_url }}">← {{ back_label }}</a></p>{% endif %}
```

## 4. The property page: `details/related.py`, `details/views.py`, `details/templates/details/detail.html`, `details/registry.py`
`details/related.py`: replace the `Section` dataclass and `_section` (they gain an optional **Add** button that keeps a section visible when empty, and `more` can now take reverse arguments):
```python
@dataclass
class Section:
    title: str
    rows: list = field(default_factory=list)
    total: int = 0
    more_url: str | None = None
    add_url: str | None = None           # "+ Add" link shown in the heading (writers only)
    add_label: str = ""
    empty_text: str = ""                 # shown instead of the section being hidden, when add_url is set


def _section(title, qs, make_row, more=None, add=None):
    """more=(url name, query string[, reverse args]); add=(url, label, empty text) keeps the section visible when empty."""
    total = qs.count()
    if not total and not add:
        return None
    more_url = None
    if more and total > LIMIT:
        more_url = reverse(more[0], args=more[2] if len(more) > 2 else None) + (("?" + more[1]) if more[1] else "")
    section = Section(title, [make_row(o) for o in qs[:LIMIT]], total, more_url)
    if add:
        section.add_url, section.add_label, section.empty_text = add
    return section
```
Change the signature of `sections_for` and its Units line (the rest of the function is unchanged):
```python
def sections_for(key, obj, can_write=False):
    """Non-empty sections for this record, in reading order. A property's Units section stays visible (with an Add button) for writers."""
    pk = obj.pk
    if key == "property":
        found = [
            _section("Units", Unit.objects.active().filter(property=obj).order_by("name"), _unit_row, ("property_units", "", [pk]),
                     add=(f"{reverse('unit_create')}?property={pk}", "+ Add unit", "No units yet.") if can_write else None),
```
`details/views.py`: replace `RecordDetail.get_context_data` (a unit's "back" is its property):
```python
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        links = _links_for_related_values(self.obj)
        name = self.kind.name(self.obj)
        rows = describe_fields(self.obj)
        lead = next((i for i, (_, value) in enumerate(rows) if value == name), None)          # Name/Title first, then the rest in model order
        if lead:
            rows.insert(0, rows.pop(lead))
        fields = [(label, value, links.get(label)) for label, value in rows]
        fields += [(label, value, None) for label, value in extra_rows(self.kind.key, self.obj, timezone.localdate())]
        can_write = self.request.membership.can_write
        if self.kind.key == "unit":                          # units have no list of their own: "back" is the property's page
            list_url, back_label = reverse("property_detail", args=[self.obj.property_id]), self.obj.property.name
        else:
            list_url, back_label = reverse(LIST_NAMES[self.kind.key]), LIST_TITLES[self.kind.key].lower()
        ctx.update(
            kind=self.kind, obj=self.obj, name=name, fields=fields, can_write=can_write,
            sections=sections_for(self.kind.key, self.obj, can_write),
            list_url=list_url, list_title=back_label,
            edit_url=reverse(EDIT_NAMES[self.kind.key], args=[self.obj.pk]) if can_write else None,
            archive_url=reverse(ARCHIVE_NAMES[self.kind.key], args=[self.obj.pk]) if can_write else None,
        )
        return ctx
```
`details/templates/details/detail.html`: three edits.
* `Back to {{ list_title|lower }}` becomes `Back to {{ list_title }}` (the view now lower-cases the list names itself).
* Replace `<h2>{{ s.title }} <small>{{ s.total }}</small></h2>` with
```html
  <h2><span>{{ s.title }} <small>{{ s.total }}</small></span>{% if s.add_url %}<a class="btn secondary" href="{{ s.add_url }}" style="font-size:.85rem;padding:.2rem .6rem">{{ s.add_label }}</a>{% endif %}</h2>
  {% if not s.rows and s.empty_text %}<p style="margin:.2rem 0;color:var(--muted)">{{ s.empty_text }}</p>{% endif %}
```
(the second line goes directly above the existing `<ul>`).
`details/registry.py`: in `LIST_NAMES` change `"unit": "unit_list"` to `"unit": "property_list"` (unit pages no longer use it; it only keeps the mapping valid).

## 5. `properties/views.py`
Add `from django.shortcuts import get_object_or_404` and `from django.urls import reverse` above the `core.crud` import. Replace everything from `class UnitList` to the end of the file with:
```python
class PropertyUnits(TenantListView):
    """A property's units: a subsection of the property, not a top-level list."""
    model = Unit
    url_basename = "unit"
    export_key = "units"
    columns = [("Unit", "name"), ("Notes", "notes")]
    search_fields = ("name", "notes")

    def dispatch(self, request, *args, **kwargs):
        self.parent = None
        return super().dispatch(request, *args, **kwargs)

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.property_pk = kwargs["property_pk"]

    def get_property(self):
        if self.parent is None:
            self.parent = get_object_or_404(Property.objects.active(), pk=self.property_pk)   # tenant-scoped: other accounts' pks 404
        return self.parent

    @property
    def title(self):
        return f"Units · {self.get_property().name}"

    @property
    def back_url(self):
        return reverse("property_detail", args=[self.property_pk])

    @property
    def back_label(self):
        return self.get_property().name

    def get_queryset(self):
        self.get_property()
        return super().get_queryset().filter(property_id=self.property_pk)

    def get_create_url(self):
        return f"{reverse('unit_create')}?property={self.property_pk}"


class UnitFormMixin:
    """Units have no list of their own: 'back' means the property they belong to."""

    def get_list_url(self):
        obj = getattr(self, "object", None)
        wanted = obj.property_id if obj is not None and obj.pk else self.request.GET.get("property", "")
        if str(wanted).isdigit() and Property.objects.active().filter(pk=wanted).exists():      # only this account's live properties
            return reverse("property_detail", args=[int(wanted)])
        return reverse("property_list")


class UnitCreate(UnitFormMixin, TenantCreateView):
    model, form_class, url_basename, title = Unit, UnitForm, "unit", "New unit"

    def get_initial(self):
        initial = super().get_initial()
        wanted = self.request.GET.get("property", "")
        if wanted.isdigit() and Property.objects.active().filter(pk=wanted).exists():
            initial["property"] = int(wanted)
        return initial

    def get_success_url(self):
        return reverse("property_units", args=[self.object.property_id])      # you were adding units: show the list you added to


class UnitUpdate(UnitFormMixin, TenantUpdateView):
    model, form_class, url_basename, title = Unit, UnitForm, "unit", "Edit unit"


class UnitArchive(TenantArchiveView):
    model, url_basename = Unit, "unit"

    def after_archive_url(self, obj):
        return reverse("property_detail", args=[obj.property_id])      # back to the property it belonged to
```

## 6. `properties/urls.py` (whole file)
```python
from django.urls import path
from django.views.generic import RedirectView

from core.crud import crud_urls

from . import views as v

urlpatterns = [
    # Units live under their property. The old top-level list is gone; its URL sends people to Properties.
    path("units/", RedirectView.as_view(pattern_name="property_list", query_string=False), name="units_moved"),
    path("units/new/", v.UnitCreate.as_view(), name="unit_create"),
    path("units/<int:pk>/", v.UnitUpdate.as_view(), name="unit_edit"),
    path("units/<int:pk>/archive/", v.UnitArchive.as_view(), name="unit_archive"),
    path("<int:property_pk>/units/", v.PropertyUnits.as_view(), name="property_units"),
] + crud_urls("", "property", v.PropertyList, v.PropertyCreate, v.PropertyUpdate, v.PropertyArchive)
```

## 7. Existing tests that encoded the old Units list
* `core/tests/test_views.py`: in `test_anonymous_redirected_to_login`, `test_all_list_pages_render_for_owner` and `IsolationTests.test_list_shows_only_own_account` replace the string `"/properties/units/"` in the URL tuples with `f"/properties/{self.maple.pk}/units/"` (keep `"/properties/units/new/"`).
* `exports/tests/test_exports.py`, `test_every_list_can_export_and_viewers_cannot`: the key `"/properties/units/": "units"` becomes `f"/properties/{self.maple.pk}/units/": "units"`.
* `planner/tests/test_dashboard.py`: remove `"Units": 0,` and `"Units": 2,` from the two expected `counts` dictionaries.
* `archive/tests/test_archive.py`, `RestoreTests.test_every_type_comes_back_exactly_as_it_was`: replace the line
  `self.assertNotContains(self.client.get(live_pages[key], HTTP_HX_REQUEST="true"), self.snapshot_name(obj))` with
```python
            list_url = f"/properties/{obj.property_id}/units/" if key == "unit" else live_pages[key]      # units are listed under their property
            self.assertNotContains(self.client.get(list_url, HTTP_HX_REQUEST="true"), self.snapshot_name(obj))
```
* `details/tests/test_details.py`:
  * `test_only_active_records_of_this_account_are_listed`: the Units assertion becomes `self.assertEqual(s["Units"], [])` (owners keep an empty Units section with its Add button); keep `assertNotIn("Events", s)`.
  * `test_long_lists_are_cut_with_an_exact_total_and_a_filtered_see_all_link`: the two Units URLs become `/properties/{pk}/units/` , that is
    `f'href="/properties/{self.objs["property"].pk}/units/">See all 12'` and `self.client.get(f"/properties/{self.objs['property'].pk}/units/")`.
  * `RowLinkTests.test_every_list_opens_the_read_only_page_and_other_lists_keep_their_own_pages`: `"unit": "/properties/units/"` becomes `"unit": f"/properties/{self.objs['property'].pk}/units/"`.

## 8. `core/management/commands/check_setup.py` (optional, recommended)
* In the URL-name tuple replace `"unit_list"` with `"property_units"`.
* In the `expected` dictionary replace `"properties.views.UnitList": "units"` with `"properties.views.PropertyUnits": "units"`.
* Add these checks just before the `TenantFormMixin.archivable` check:
```python
    add("units live under their property", lambda: hasattr(mod("properties.views"), "PropertyUnits") and not hasattr(mod("properties.views"), "UnitList"), "Units patch, section 5: replace UnitList with PropertyUnits (and the unit form/archive views) in properties/views.py.")
    add("TenantArchiveView.after_archive_url", lambda: hasattr(mod("core.crud").TenantArchiveView, "after_archive_url"), "Units patch, section 2: add after_archive_url to TenantArchiveView and use it in post.")
    add("TenantFormMixin.get_list_url", lambda: hasattr(F(), "get_list_url"), "Units patch, section 2: add get_list_url to TenantFormMixin and use it for list_url, get_success_url and cancel_url.")
    add("TenantListView.get_create_url and back link", lambda: hasattr(L(), "get_create_url") and hasattr(L(), "back_url"), "Units patch, section 2: add back_url/back_label, get_create_url and the new context keys to TenantListView.")
```
* Add these three just before the `crud/_results.html link_rows` check:
```python
    add("base.html has no Units tab", lambda: "unit_list" not in template_text("base.html"), "Units patch, section 1: delete the `<a href=\"{% url 'unit_list' %}\">Units</a>` line from templates/base.html.")
    add("crud/list.html back link", t("crud/list.html", "back_url"), "Units patch, section 3: add the back_url line above <h1> in templates/crud/list.html.")
    add("record page section Add button", t("details/detail.html", "add_url"), "Units patch, section 4: update templates/details/detail.html (h2 with add_url, empty_text).")
```
