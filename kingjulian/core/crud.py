"""Small generic CRUD layer so each entity is ~10 lines of view code.

Conventions: a view sets `url_basename = "property"` and gets URL names
property_list / property_create / property_edit / property_archive.
"""
from dataclasses import dataclass
from typing import Callable

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from .tenancy import require_current_account_id


@dataclass
class Filter:
    param: str                       # query-string key
    field: str                       # ORM lookup, e.g. "category" or "property_id"
    label: str
    choices: Callable[[], list]      # -> [(value, label), ...]; tenant-scoped when FK-based
    default: str = ""                # applied when the param is absent from the query string
    apply: Callable | None = None    # custom (queryset, value) -> queryset instead of field=value


class TenantViewMixin(LoginRequiredMixin):
    url_basename: str = ""
    requires_write = False

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            if request.membership is None:
                raise PermissionDenied("Your user has no account.")
            if self.requires_write and not request.membership.can_write:
                raise PermissionDenied("Read-only role.")
        return super().dispatch(request, *args, **kwargs)

    def url(self, suffix, *args):
        return reverse(f"{self.url_basename}_{suffix}", args=args)


class TenantListView(TenantViewMixin, ListView):
    title = ""
    columns: list = []               # [(label, "attr.path"), ...]; first column links to edit
    search_fields: tuple = ()
    filters: list = []
    paginate_by = 25
    template_name = "crud/list.html"
    partial_template_name = "crud/_results.html"

    def get_template_names(self):
        return [self.partial_template_name if self.request.headers.get("HX-Request") else self.template_name]

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


class TenantFormMixin(TenantViewMixin):
    requires_write = True
    template_name = "crud/form.html"
    title = ""

    def get_success_url(self):
        return self.url("list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = self.title
        ctx["list_url"] = self.url("list")
        if self.archivable and getattr(self, "object", None) and self.object.pk:
            ctx["archive_url"] = self.url("archive", self.object.pk)
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"Saved {self.object}.")
        return response


class TenantCreateView(TenantFormMixin, CreateView):
    def form_valid(self, form):
        form.instance.account_id = require_current_account_id()
        return super().form_valid(form)


class TenantUpdateView(TenantFormMixin, UpdateView):
    def get_queryset(self):
        return self.model.objects.active()   # tenant-scoped: other accounts' pks 404


class TenantArchiveView(TenantViewMixin, View):
    requires_write = True
    model = None

    def post(self, request, pk):
        obj = get_object_or_404(self.model.objects.active(), pk=pk)
        obj.archive()
        messages.success(request, f"Archived {obj}.")
        return redirect(self.url("list"))


def crud_urls(prefix, basename, list_view, create_view, update_view, archive_view):
    base = f"{prefix.strip('/')}/" if prefix.strip("/") else ""
    return [
        path(base, list_view.as_view(), name=f"{basename}_list"),
        path(f"{base}new/", create_view.as_view(), name=f"{basename}_create"),
        path(f"{base}<int:pk>/", update_view.as_view(), name=f"{basename}_edit"),
        path(f"{base}<int:pk>/archive/", archive_view.as_view(), name=f"{basename}_archive"),
    ]
