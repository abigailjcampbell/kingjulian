# Step 3 patches to existing files

New files are in `maintenance-step3.zip` (unzip into the project root). Everything below is a change to a file
that already existed, given as the changed function or snippet only.

## 1. `config/settings.py`
Add `"maintenance"` to `INSTALLED_APPS` (after `"inventory"`), and append:
```python
# Email: console in dev; set EMAIL_BACKEND/EMAIL_HOST/... in production.
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "Property Manager <noreply@localhost>")
SITE_URL = os.environ.get("SITE_URL", "http://localhost:8000")   # used for links inside emails
```

## 2. `config/urls.py`
```python
    path("maintenance/", include("maintenance.urls")),
```

## 3. `requirements.txt`
```
python-dateutil>=2.9
```

## 4. `core/crud.py`

`Filter` gains a `default` (the maintenance list defaults to open work):
```python
@dataclass
class Filter:
    param: str                       # query-string key
    field: str                       # ORM lookup, e.g. "category" or "property_id"
    label: str
    choices: Callable[[], list]      # -> [(value, label), ...]; tenant-scoped when FK-based
    default: str = ""                # applied when the param is absent from the query string
```

`TenantListView`: add the class attributes `has_create = True` and `viewers_can_open = False` next to `paginate_by`, then:
```python
    def get_queryset(self):
        qs = self.model.objects.active()
        q = self.request.GET.get("q", "").strip()
        if q and self.search_fields:
            cond = Q()
            for f in self.search_fields:
                cond |= Q(**{f"{f}__icontains": q})
            qs = qs.filter(cond)
        for flt in self.filters:
            value = self.request.GET.get(flt.param, flt.default)
            # Only accept values that are in the (tenant-scoped) choice list.
            if value and value in {str(v) for v, _ in flt.choices()}:
                qs = qs.filter(**{flt.field: value})
        return qs

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
            edit_url_name=f"{self.url_basename}_edit",
        )
        return ctx
```

`TenantFormMixin`: add the class attribute `archivable = True` below `requires_write = True`, then:
```python
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = self.title
        ctx["list_url"] = self.url("list")
        if self.archivable and getattr(self, "object", None) and self.object.pk:
            ctx["archive_url"] = self.url("archive", self.object.pk)
        return ctx
```

## 5. Templates (one line each)
* `templates/base.html`: after the Inventory link in the nav add
  `<a href="{% url 'occurrence_list' %}">Maintenance</a>` and `<a href="{% url 'task_list' %}">Tasks</a>`.
* `templates/crud/list.html`: `All {{ f.label|lower }}s` becomes `Any {{ f.label|lower }}` (it rendered "All propertys").
  Also change `{% if request.membership.can_write %}<a class="btn" href="{{ create_url }}"` to
  `{% if create_url and request.membership.can_write %}<a class="btn" href="{{ create_url }}"`.
* `templates/crud/_results.html`: `{% if forloop.first %}<a href` becomes `{% if forloop.first and link_rows %}<a href`.

## 6. Bug fixed in steps 1-2: read-only users were linked to pages that returned 403
Every list row linked to its edit page, which viewers can't open. Items 4 and 5 (`link_rows`) fix it. Regression test, add to
`core/tests/test_views.py`:
```python
class RowLinkTests(World):
    def test_viewers_are_not_offered_links_to_pages_that_403(self):
        edit_links = [f'href="/properties/{self.maple.pk}/"', f'href="/contacts/{self.plumber.pk}/"',
                      f'href="/inventory/{self.heater.pk}/"']
        self.login(self.vic)
        for url, link in zip(("/properties/", "/contacts/", "/inventory/"), edit_links):
            self.assertNotContains(self.client.get(url), link)
            self.assertEqual(self.client.get(link.split('"')[1]).status_code, 403)   # and the page really is forbidden
        self.client.logout(); self.login(self.alice)
        for url, link in zip(("/properties/", "/contacts/", "/inventory/"), edit_links):
            self.assertContains(self.client.get(url), link)

    def test_filter_dropdowns_read_naturally(self):
        self.login(self.alice)
        body = self.client.get("/inventory/").content.decode()
        self.assertIn("Any property", body); self.assertNotIn("propertys", body)
```
