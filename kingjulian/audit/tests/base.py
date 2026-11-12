from audit.models import ActivityLog
from attachments.tests.base import FilesWorld
from core.tenancy import use_account


class AuditWorld(FilesWorld):
    """World's own setup creates rows (and therefore log entries); start every test from a clean log."""

    def setUp(self):
        ActivityLog.all_tenants.all().delete()          # queryset delete: allowed in tests; see the model's append-only note

    def log(self, account=None, **filters):
        """Entries for an account, oldest first."""
        with use_account(account or self.acme):
            return list(ActivityLog.objects.filter(**filters).order_by("id"))

    def actions(self, account=None, **filters):
        return [e.action for e in self.log(account, **filters)]
