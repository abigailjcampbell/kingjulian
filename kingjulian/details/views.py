from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.text import capfirst
from django.views.generic import TemplateView

from archive.registry import KINDS
from archive.services import describe_fields
from core.crud import TenantViewMixin

from .registry import ARCHIVE_NAMES, EDIT_NAMES, LIST_NAMES, LIST_TITLES, record_url
from .related import extra_rows, sections_for


def _links_for_related_values(obj):
    """{field label: url} for foreign-key fields that point at another record with its own page."""
    links = {}
    for f in obj._meta.concrete_fields:
        if f.many_to_one and getattr(obj, f.attname) is not None:
            related = getattr(obj, f.name)
            url = record_url(related)
            if url:
                links[capfirst(str(f.verbose_name))] = url
    return links


class RecordDetail(TenantViewMixin, TemplateView):
    """A record's read-only page. Every role can open it; owners and managers get an Edit button."""

    template_name = "details/detail.html"
    kind_key = None                         # set per route in urls.py

    def get(self, request, *args, **kwargs):
        kind = KINDS[self.kind_key]
        obj = get_object_or_404(kind.model.objects.all(), pk=kwargs["pk"])         # tenant-scoped; includes archived rows
        if obj.archived_at is not None:                                              # archived records live on the Archive page
            if request.membership.can_write:
                return redirect(reverse("archive_detail", args=[kind.key, obj.pk]))
            raise Http404("Not found.")
        self.kind, self.obj = kind, obj
        return super().get(request, *args, **kwargs)

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