# Step 4 patches to existing files

New files are in `planner-step4.zip` (unzip into the project root; it also adds `requirements-dev.txt`).
Below is every change to a file that already existed, as the changed function or snippet only.

## 1. `config/settings.py`
Add `"planner"` to `INSTALLED_APPS`, after `"maintenance"`.

## 2. `config/urls.py`
```python
    path("planner/", include("planner.urls")),
```

## 3. `core/urls.py`: the home page becomes the dashboard
```python
from django.urls import path

from planner.views import DashboardView

urlpatterns = [path("", DashboardView.as_view(), name="home")]
```
`HomeView` in `core/views.py` and `templates/home.html` are now unused and can be deleted.

## 4. `core/crud.py`
`Filter` gains `apply`, a custom (queryset, value) hook used by the events list's "upcoming / past" filter:
```python
@dataclass
class Filter:
    param: str                       # query-string key
    field: str                       # ORM lookup, e.g. "category" or "property_id"
    label: str
    choices: Callable[[], list]      # -> [(value, label), ...]; tenant-scoped when FK-based
    default: str = ""                # applied when the param is absent from the query string
    apply: Callable | None = None    # custom (queryset, value) -> queryset instead of field=value
```
and one line changes inside `TenantListView.get_queryset`:
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
                qs = flt.apply(qs, value) if flt.apply else qs.filter(**{flt.field: value})
        return qs
```

## 5. `templates/base.html`
After the Tasks link in the nav:
```html
    <a href="{% url 'calendar' %}">Calendar</a>
```

## 6. Install the test dependency
`pip install -r requirements-dev.txt` (adds `icalendar`, used only by the tests), then `python manage.py migrate`.
