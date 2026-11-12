from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import TemplateView

from core.crud import Filter, TenantListView, TenantViewMixin

from .forms import UploadForm
from .models import Attachment
from .services import delete_attachment, store_attachment
from .targets import EDIT_URL_NAME, FIELDS, TARGETS


def _target_page(att_or_kind, pk=None, fallback="attachment_list"):
    if isinstance(att_or_kind, Attachment):
        return reverse(att_or_kind.target_url_name, args=[att_or_kind.target.pk])
    return reverse(EDIT_URL_NAME[att_or_kind], args=[pk])


def _safe_next(request, default):
    nxt = request.POST.get("next") or request.GET.get("next") or ""
    ok = url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure())
    return nxt if ok and nxt.startswith("/") else default


# ---- serving files ---------------------------------------------------------------------------
def _send(att, *, inline):
    """Serve a stored file with headers that stop a browser from ever treating it as a page."""
    try:
        handle = att.file.open("rb")
    except (FileNotFoundError, ValueError):
        raise Http404("File is missing from storage.")
    response = FileResponse(handle, content_type=att.content_type, as_attachment=not inline, filename=att.original_name)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


class AttachmentDownload(TenantViewMixin, View):
    def get(self, request, pk):
        return _send(get_object_or_404(Attachment.objects.all(), pk=pk), inline=False)      # tenant-scoped: 404 across accounts


class AttachmentInline(TenantViewMixin, View):
    """Inline display, images only (the allow-list sets Kind.inline). Everything else must be downloaded."""

    def get(self, request, pk):
        att = get_object_or_404(Attachment.objects.all(), pk=pk)
        if not att.is_inline_safe:
            raise Http404("Not displayable inline.")
        return _send(att, inline=True)


# ---- upload / delete ---------------------------------------------------------------------------
class AttachmentUpload(TenantViewMixin, View):
    requires_write = True

    def post(self, request, kind, pk):
        if kind not in TARGETS:
            raise Http404
        model, _ = TARGETS[kind]
        target = get_object_or_404(model.objects.active(), pk=pk)           # tenant-scoped; archived targets are gone
        back = _safe_next(request, _target_page(kind, pk))

        form = UploadForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, " ".join(m for errs in form.errors.values() for m in errs))
            return redirect(back)
        try:
            att = store_attachment(target=target, uploaded=form.cleaned_data["file"], user=request.user,
                                   caption=form.cleaned_data["caption"])
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, f"Uploaded {att.original_name}.")
        return redirect(back)


class AttachmentDelete(TenantViewMixin, View):
    requires_write = True

    def post(self, request, pk):
        att = get_object_or_404(Attachment.objects.all(), pk=pk)
        back = _safe_next(request, _target_page(att))
        name = att.original_name
        delete_attachment(att)
        messages.success(request, f"Deleted {name}.")
        return redirect(back)


# ---- browsing ---------------------------------------------------------------------------------
def _kind_choices():
    return [(kind, {"item": "Inventory item", "task": "Maintenance task", "occurrence": "Maintenance job"}.get(kind, kind.title()))
            for kind in TARGETS]


def _apply_kind(qs, value):
    return qs.filter(**{f"{TARGETS[value][1]}__isnull": False})


class AttachmentList(TenantListView):
    model = Attachment
    url_basename = "attachment"
    title = "Files"
    has_create = False
    viewers_can_open = True
    export_key = "attachments"
    columns = [("File", "original_name"), ("For", "for_label"), ("Size", "size_label"),
               ("Uploaded by", "uploader_label"), ("Added", "created_at")]
    search_fields = ("original_name", "caption", "property__name", "item__name", "task__title", "occurrence__task__title",
                     "contact__name", "event__title")
    filters = [Filter("kind", "", "Attached to", _kind_choices, apply=_apply_kind)]

    def get_queryset(self):
        return (super().get_queryset().select_related("uploaded_by", "property", "item", "task", "occurrence__task",
                                                      "contact", "event"))


class AttachmentDetail(TenantViewMixin, TemplateView):
    template_name = "attachments/detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        att = get_object_or_404(Attachment.objects.select_related("uploaded_by", "occurrence__task"), pk=self.kwargs["pk"])
        can_write = self.request.membership.can_write
        ctx.update(a=att, can_write=can_write,
                   target_url=_target_page(att) if (can_write or att.target_kind == "occurrence") else None)
        return ctx
