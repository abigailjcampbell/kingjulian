from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import TemplateView

from accounts.models import Membership
from core.crud import TenantViewMixin

from .deletion import CONFIRM_WORD, LIVE_EDIT, DeleteBlocked, NotArchived, build_plan, delete_forever
from .views import _kind_or_404


class ArchiveDelete(TenantViewMixin, TemplateView):
    """GET: the 'are you sure' page. POST: the deletion, only with the confirmation word and an unchanged plan. Owners only."""

    template_name = "archive/delete.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.membership and request.membership.role != Membership.Role.OWNER:
            raise PermissionDenied("Only owners can permanently delete records.")
        return super().dispatch(request, *args, **kwargs)

    def _load(self, request, kind_key, pk):
        """(kind, obj) or a redirect response. A missing record is reported neutrally (scoping means it may be someone else's)."""
        kind = _kind_or_404(kind_key)
        obj = kind.model.objects.filter(pk=pk).first()
        if obj is None:
            messages.info(request, "That record no longer exists.")
            return kind, redirect("archive_list")
        if obj.archived_at is None:
            messages.info(request, "Only archived records can be permanently deleted. Archive it first if you really mean to remove it.")
            return kind, redirect(reverse(LIVE_EDIT[kind.key], args=[obj.pk]))
        return kind, obj

    def _render(self, request, kind, obj, error=""):
        plan = build_plan(obj)
        ctx = self.get_context_data(kind=kind, obj=obj, name=kind.name(obj), plan=plan, word=CONFIRM_WORD, error=error)
        return self.render_to_response(ctx)

    def get(self, request, kind, pk):
        kind, obj = self._load(request, kind, pk)
        return obj if hasattr(obj, "status_code") else self._render(request, kind, obj)

    def post(self, request, kind, pk):
        kind, obj = self._load(request, kind, pk)
        if hasattr(obj, "status_code"):
            return obj
        plan = build_plan(obj)
        if request.POST.get("confirm", "").strip().upper() != CONFIRM_WORD:
            return self._render(request, kind, obj, error=f"Type {CONFIRM_WORD} to confirm.")
        if request.POST.get("token") != plan.token:                         # what would be deleted changed since the page was shown
            messages.warning(request, "This record changed while you were looking at it. Please review what will be deleted and confirm again.")
            return redirect(reverse("archive_delete", args=[kind.key, obj.pk]))
        name = kind.name(obj)
        try:
            delete_forever(obj)
        except DeleteBlocked:
            return self._render(request, kind, obj)
        except NotArchived:
            return redirect(reverse(LIVE_EDIT[kind.key], args=[obj.pk]))
        messages.success(request, f"Permanently deleted {kind.label.lower()} “{name}”.")
        return redirect("archive_list")
