from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import TemplateView

from audit.models import ActivityLog
from core.crud import TenantViewMixin

from .registry import KINDS, detail_url
from .services import RestoreBlocked, blockers, describe_blocker, describe_fields, restore_record

PER_PAGE = 25
MAX_PER_KIND = 1000        # safety cap per record type; the page says so if it is ever reached


def _kind_or_404(key):
    kind = KINDS.get(key)
    if kind is None:
        raise Http404("Unknown record type.")
    return kind


def _safe_next(request, default):
    nxt = request.POST.get("next") or ""
    ok = url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure())
    return nxt if ok and nxt.startswith("/") else default


def archived_by(pairs):
    """{(type, id): email of whoever last archived it} from the activity log (one query for the whole page)."""
    if not pairs:
        return {}
    types = {t for t, _ in pairs}
    ids = {i for _, i in pairs}
    out = {}
    for e in ActivityLog.objects.filter(action="archived", target_type__in=types, target_id__in=ids).order_by("id"):
        out[(e.target_type, e.target_id)] = e.actor_email or "System"
    return out


class ArchiveList(TenantViewMixin, TemplateView):
    """Everything that has been archived, across all record types. Owners and managers only."""

    requires_write = True
    template_name = "archive/list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        chosen = self.request.GET.get("type", "")
        chosen = chosen if chosen in KINDS else ""
        q = self.request.GET.get("q", "").strip()
        rows, counts, capped = [], {}, False
        for key, kind in KINDS.items():
            base = kind.model.objects.archived()
            counts[key] = base.count()
            if chosen and key != chosen:
                continue
            qs = base
            if q:
                cond = Q()
                for lookup in kind.search:
                    cond |= Q(**{f"{lookup}__icontains": q})
                qs = qs.filter(cond)
            if kind.select_related:
                qs = qs.select_related(*kind.select_related)
            fetched = list(qs.order_by("-archived_at", "-id")[:MAX_PER_KIND])
            capped = capped or len(fetched) == MAX_PER_KIND
            rows += [{"kind": kind, "obj": o, "name": kind.name(o), "where": kind.where(o), "archived_at": o.archived_at,
                      "type_key": o._meta.model_name} for o in fetched]
        rows.sort(key=lambda r: (r["archived_at"], r["obj"].pk), reverse=True)
        page = Paginator(rows, PER_PAGE).get_page(self.request.GET.get("page"))
        who = archived_by({(r["type_key"], r["obj"].pk) for r in page.object_list})
        for r in page.object_list:
            r["by"] = who.get((r["type_key"], r["obj"].pk), "—")
            r["detail_url"] = reverse("archive_detail", args=[r["kind"].key, r["obj"].pk])
            r["restore_url"] = reverse("archive_restore", args=[r["kind"].key, r["obj"].pk])
        params = self.request.GET.copy()
        params.pop("page", None)
        ctx.update(page=page, chosen=chosen, q=q, capped=capped, total=sum(counts.values()), querystring=params.urlencode(),
                   type_options=[(k, kind.label, counts[k]) for k, kind in KINDS.items()])
        return ctx


class ArchivedDetail(TenantViewMixin, TemplateView):
    requires_write = True
    template_name = "archive/detail.html"

    def get(self, request, *args, **kwargs):
        kind = _kind_or_404(kwargs["kind"])
        obj = get_object_or_404(kind.model.objects.all(), pk=kwargs["pk"])      # tenant-scoped; includes archived rows
        if obj.archived_at is None:                                              # restored in the meantime: go to the live page
            return redirect(reverse(self.edit_names[kind.key], args=[obj.pk]))
        self.kind, self.obj = kind, obj
        return super().get(request, *args, **kwargs)

    edit_names = {"property": "property_detail", "unit": "unit_detail", "item": "inventoryitem_detail", "contact": "contact_detail",
                  "task": "task_detail", "event": "event_detail"}          # a live record's read-only page

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pending = blockers(self.obj)
        who = archived_by({(self.obj._meta.model_name, self.obj.pk)})
        ctx.update(obj=self.obj, kind=self.kind, name=self.kind.name(self.obj), fields=describe_fields(self.obj),
                   archived_by=who.get((self.obj._meta.model_name, self.obj.pk), "—"),
                   blockers=[{"text": describe_blocker(r), "url": detail_url(r)} for r in pending])
        return ctx


class ArchiveRestore(TenantViewMixin, View):
    requires_write = True

    def post(self, request, kind, pk):
        kind = _kind_or_404(kind)
        obj = get_object_or_404(kind.model.objects.all(), pk=pk)
        name = kind.name(obj)
        back = _safe_next(request, reverse("archive_list"))
        try:
            changed = restore_record(obj)
        except RestoreBlocked as exc:
            messages.error(request, f"Couldn't restore “{name}”. " + " ".join(exc.reasons))
            return redirect(reverse("archive_detail", args=[kind.key, obj.pk]))
        if changed:
            messages.success(request, f"Restored {kind.label.lower()} “{name}”.")
        else:
            messages.info(request, f"“{name}” is already active.")
        return redirect(back)
