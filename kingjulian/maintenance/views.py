from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F, OuterRef, Subquery
from django.shortcuts import get_object_or_404, redirect
from django.utils.functional import cached_property
from django.views import View
from django.views.generic import FormView, UpdateView

from core.crud import (Filter, TenantArchiveView, TenantCreateView, TenantFormMixin, TenantListView,
                       TenantUpdateView, TenantViewMixin)
from properties.models import Property

from .forms import CompleteForm, OccurrenceForm, TaskForm
from .models import MaintenanceTask, TaskOccurrence
from .services import AlreadyClosed, complete_occurrence, ensure_first_occurrence, skip_occurrence

OPEN = TaskOccurrence.Status.OPEN


def _property_choices():
    return [(p.pk, p.name) for p in Property.objects.active()]


# ---- tasks (the templates) ---------------------------------------------------------------------
class TaskList(TenantListView):
    model = MaintenanceTask
    url_basename = "task"
    title = "Maintenance tasks"
    export_key = "tasks"
    columns = [("Task", "title"), ("Property", "property.name"), ("Item", "item.name"),
               ("Repeats", "schedule_label"), ("Next due", "next_due"), ("Assigned to", "assigned_contact.name")]
    search_fields = ("title", "description", "property__name", "item__name", "assigned_contact__name")
    filters = [Filter("property", "property_id", "Property", _property_choices)]

    def get_queryset(self):
        next_open = TaskOccurrence.objects.filter(task=OuterRef("pk"), status=OPEN).values("due_date")[:1]
        return (super().get_queryset()
                .select_related("property", "item", "assigned_contact")
                .annotate(next_due=Subquery(next_open))
                .order_by(F("next_due").asc(nulls_last=True), "title"))


class TaskCreate(TenantCreateView):
    model, form_class, url_basename, title = MaintenanceTask, TaskForm, "task", "New maintenance task"

    def form_valid(self, form):
        with transaction.atomic():
            response = super().form_valid(form)
            ensure_first_occurrence(self.object)
        return response


class TaskUpdate(TenantUpdateView):
    model, form_class, url_basename, title = MaintenanceTask, TaskForm, "task", "Edit maintenance task"


class TaskArchive(TenantArchiveView):
    model, url_basename = MaintenanceTask, "task"


# ---- occurrences (the to-do list) ------------------------------------------------------------
class OccurrenceList(TenantListView):
    model = TaskOccurrence
    url_basename = "occurrence"
    title = "Maintenance"
    export_key = "history"
    has_create = False
    viewers_can_open = True
    columns = [("Due", "due_date"), ("When", "due_label"), ("Task", "task.title"),
               ("Property", "task.property.name"), ("Item", "task.item.name"),
               ("Assigned to", "task.assigned_contact.name")]
    search_fields = ("task__title", "task__property__name", "task__item__name", "notes")
    filters = [
        Filter("status", "status", "Status", lambda: list(TaskOccurrence.Status.choices), default="open"),
        Filter("property", "task__property_id", "Property", _property_choices),
    ]

    def get_queryset(self):
        qs = (super().get_queryset().filter(task__archived_at__isnull=True)
              .select_related("task__property", "task__item", "task__assigned_contact"))
        # Open work: soonest first. History: newest first.
        return qs if self.request.GET.get("status", "open") == "open" else qs.order_by("-due_date", "-id")


class OccurrenceUpdate(TenantFormMixin, UpdateView):
    model, form_class, url_basename = TaskOccurrence, OccurrenceForm, "occurrence"
    template_name = "maintenance/occurrence_form.html"
    requires_write = False          # read-only roles may view; saving is enforced in post()
    archivable = False              # occurrences are closed (complete/skip), never archived

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if not self.request.membership.can_write:
            for field in form.fields.values():
                field.disabled = True
        return form

    def post(self, request, *args, **kwargs):
        if not request.membership.can_write:
            raise PermissionDenied("Read-only role.")
        return super().post(request, *args, **kwargs)

    def get_queryset(self):
        return TaskOccurrence.objects.active().select_related("task__property", "task__unit", "task__item",
                                                              "task__assigned_contact")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = f"{self.object.task.title}"
        ctx["is_open"] = self.object.status == OPEN
        return ctx


class _OccurrenceAction(TenantViewMixin):
    requires_write = True
    url_basename = "occurrence"

    @cached_property
    def occurrence(self):
        # Tenant-scoped manager: another account's pk is a 404.
        return get_object_or_404(TaskOccurrence.objects.select_related("task__property"),
                                 pk=self.kwargs["pk"])


class OccurrenceComplete(_OccurrenceAction, FormView):
    form_class = CompleteForm
    template_name = "maintenance/complete.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(occurrence=self.occurrence, title="Mark complete", list_url=self.url("list"))
        return ctx

    def form_valid(self, form):
        try:
            _, nxt = complete_occurrence(
                self.occurrence, completed_on=form.cleaned_data["completed_on"], user=self.request.user,
                cost=form.cleaned_data["cost"], notes=form.cleaned_data["notes"])
        except AlreadyClosed:
            messages.warning(self.request, "That one was already closed.")
        else:
            msg = f"Completed {self.occurrence.task.title}."
            if nxt:
                msg += f" Next due {nxt.due_date:%b %d, %Y}."
            messages.success(self.request, msg)
        return redirect(self.url("list"))


class OccurrenceSkip(_OccurrenceAction, View):
    def post(self, request, pk):
        try:
            _, nxt = skip_occurrence(self.occurrence, user=request.user)
        except AlreadyClosed:
            messages.warning(request, "That one was already closed.")
        else:
            messages.success(request, f"Skipped. Next due {nxt.due_date:%b %d, %Y}." if nxt else "Skipped.")
        return redirect(self.url("list"))
