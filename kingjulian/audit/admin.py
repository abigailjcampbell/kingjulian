from django.contrib import admin

from core.admin import TenantAdmin

from .models import ActivityLog


@admin.register(ActivityLog)
class ActivityLogAdmin(TenantAdmin):
    """Read-only: the log is append-only."""

    list_display = ("created_at", "actor_email", "action", "summary", "account")
    list_filter = ("action", "account")
    search_fields = ("summary", "actor_email", "target_label")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
