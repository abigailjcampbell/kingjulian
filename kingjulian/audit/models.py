from django.conf import settings
from django.db import models

from core.models import TenantModel


class ImmutableLog(RuntimeError):
    """Activity entries are append-only."""


class ActivityLog(TenantModel):
    class Action(models.TextChoices):
        CREATED = "created", "Created"
        UPDATED = "updated", "Updated"
        ARCHIVED = "archived", "Archived"
        RESTORED = "restored", "Restored"
        DELETED = "deleted", "Deleted"
        UPLOADED = "uploaded", "Uploaded file"
        COMPLETED = "completed", "Completed"
        SKIPPED = "skipped", "Skipped"
        REOPENED = "reopened", "Reopened"
        LINKED = "linked", "Linked"
        UNLINKED = "unlinked", "Unlinked"
        EXPORTED = "exported", "Exported data"

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    actor_email = models.CharField(max_length=254, blank=True)       # snapshot, survives user deletion
    action = models.CharField(max_length=20, choices=Action.choices)
    # Plain strings, not foreign keys: the log must outlive the rows it describes.
    target_type = models.CharField(max_length=40, blank=True)         # model_name, e.g. "inventoryitem"
    target_id = models.PositiveBigIntegerField(null=True, blank=True)
    target_label = models.CharField(max_length=200, blank=True)
    summary = models.CharField(max_length=400)
    changes = models.JSONField(default=dict, blank=True)               # {"Condition": ["Good", "Poor"], ...}

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["account", "created_at"]),
                   models.Index(fields=["account", "target_type", "target_id"])]

    def __str__(self):
        return self.summary

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableLog("Activity entries cannot be edited.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableLog("Activity entries cannot be deleted.")

    @property
    def actor_label(self):
        return self.actor_email or "System"

    @property
    def changes_label(self):
        return "; ".join(f"{k}: {old or '—'} → {new or '—'}" for k, (old, new) in self.changes.items())
