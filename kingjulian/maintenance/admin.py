from django.contrib import admin

from core.admin import TenantAdmin

from .models import MaintenanceTask, Reminder, TaskOccurrence


@admin.register(MaintenanceTask)
class MaintenanceTaskAdmin(TenantAdmin):
    list_display = ("title", "property", "recurrence_rule", "schedule_mode", "account", "archived_at")
    search_fields = ("title",)


@admin.register(TaskOccurrence)
class TaskOccurrenceAdmin(TenantAdmin):
    list_display = ("task", "due_date", "status", "completed_on", "cost", "account")
    list_filter = ("status", "account")


@admin.register(Reminder)
class ReminderAdmin(TenantAdmin):
    list_display = ("occurrence", "kind", "remind_on", "status", "attempts", "sent_at", "account")
    list_filter = ("status", "kind", "account")
