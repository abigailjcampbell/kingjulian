import re

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View

from core.crud import Filter, TenantListView, TenantViewMixin
from core.tenancy import require_current_account_id

from .models import ActivityLog

User = get_user_model()

TYPE_LABELS = {"property": "Property", "unit": "Unit", "contact": "Contact", "inventoryitem": "Inventory item",
               "maintenancetask": "Maintenance task", "taskoccurrence": "Maintenance job", "event": "Event",
               "attachment": "File", "export": "Export"}
JUMP = {"property": "property_edit", "unit": "unit_edit", "contact": "contact_edit", "inventoryitem": "inventoryitem_edit",
        "maintenancetask": "task_edit", "taskoccurrence": "occurrence_edit", "event": "event_edit"}
OBJECT_PARAM = re.compile(r"([a-z]+):([0-9]+)")


def _people():
    return [(u.pk, u.email) for u in User.objects.filter(memberships__account_id=require_current_account_id())
            .order_by("email").distinct()]


class ActivityList(TenantListView):
    """Owners and managers only (viewers get a 403)."""

    model = ActivityLog
    url_basename = "activity"
    title = "Activity"
    requires_write = True
    has_create = False
    paginate_by = 50
    export_key = "activity"
    columns = [("When", "created_at"), ("Who", "actor_label"), ("What", "summary"), ("Changes", "changes_label")]
    search_fields = ("summary", "target_label", "actor_email")
    filters = [
        Filter("action", "action", "Action", lambda: list(ActivityLog.Action.choices)),
        Filter("type", "target_type", "Type", lambda: list(TYPE_LABELS.items())),
        Filter("who", "actor_id", "Person", _people),
    ]

    def get_queryset(self):
        qs = super().get_queryset()
        m = OBJECT_PARAM.fullmatch(self.request.GET.get("object", ""))       # ?object=property:12 (from a record's history panel)
        return qs.filter(target_type=m.group(1), target_id=int(m.group(2))) if m else qs


class ActivityJump(TenantViewMixin, View):
    """Open the record an entry is about, if it still exists."""

    requires_write = True

    def get(self, request, pk):
        entry = get_object_or_404(ActivityLog.objects.all(), pk=pk)
        name = JUMP.get(entry.target_type)
        if name and entry.target_id and self._exists(entry):
            return redirect(reverse(name, args=[entry.target_id]))
        messages.info(request, "That record no longer exists.")
        return redirect("activity_list")

    @staticmethod
    def _exists(entry):
        from audit.registry import SPECS
        model = next((m for m in SPECS if m._meta.model_name == entry.target_type), None)
        return bool(model and model.objects.active().filter(pk=entry.target_id).exists())
