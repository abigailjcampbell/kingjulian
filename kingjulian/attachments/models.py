import builtins
import uuid
from functools import reduce
from operator import or_

from django.conf import settings
from django.db import models
from django.db.models import Q

from contacts.models import Contact
from core.models import TenantModel
from inventory.models import InventoryItem
from maintenance.models import MaintenanceTask, TaskOccurrence
from planner.models import Event
from properties.models import Property

from .targets import EDIT_URL_NAME, FIELDS
from .validators import KINDS


def upload_path(instance, filename):
    """Random, unguessable, tenant-prefixed. The user's filename never touches the filesystem path."""
    return f"attachments/{instance.account_id}/{uuid.uuid4().hex}.{instance.stored_ext}"


def _exactly_one_target():
    clauses = []
    for field in FIELDS:
        clause = Q(**{f"{field}__isnull": False})
        for other in FIELDS:
            if other != field:
                clause &= Q(**{f"{other}__isnull": True})
        clauses.append(clause)
    return reduce(or_, clauses)


class Attachment(TenantModel):
    """A file belonging to exactly one property, item, task, occurrence, contact, or event.

    Each target is a real foreign key (not a generic relation), so the database guarantees integrity and
    TenantModel's cross-account check covers it. A CHECK constraint enforces 'exactly one target'.
    """

    file = models.FileField(upload_to=upload_path, max_length=255)
    original_name = models.CharField(max_length=150)
    content_type = models.CharField(max_length=100)
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    caption = models.CharField(max_length=200, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="+")

    property = models.ForeignKey(Property, on_delete=models.PROTECT, null=True, blank=True, related_name="attachments")
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, null=True, blank=True, related_name="attachments")
    task = models.ForeignKey(MaintenanceTask, on_delete=models.PROTECT, null=True, blank=True, related_name="attachments")
    occurrence = models.ForeignKey(TaskOccurrence, on_delete=models.PROTECT, null=True, blank=True, related_name="attachments")
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, null=True, blank=True, related_name="attachments")
    event = models.ForeignKey(Event, on_delete=models.PROTECT, null=True, blank=True, related_name="attachments")

    stored_ext = "bin"          # set by the storage service before save; only used to build the on-disk name

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(condition=_exactly_one_target(), name="attachment_exactly_one_target"),
        ]
        indexes = [models.Index(fields=["account", "created_at"])]

    def __str__(self):
        return self.original_name

    # `property` is a model field here, so reach the builtin decorator explicitly.
    @builtins.property
    def target_kind(self):
        return next(f for f in FIELDS if getattr(self, f"{f}_id") is not None)

    @builtins.property
    def target(self):
        return getattr(self, self.target_kind)

    @builtins.property
    def target_label(self):
        t = self.target
        return str(t.task.title if self.target_kind == "occurrence" else t)

    @builtins.property
    def target_type_label(self):
        return {"item": "Inventory item", "task": "Maintenance task", "occurrence": "Maintenance job"}.get(
            self.target_kind, self.target_kind.title())

    @builtins.property
    def for_label(self):
        return f"{self.target_type_label}: {self.target_label}"

    @builtins.property
    def target_url_name(self):
        return EDIT_URL_NAME[self.target_kind]

    @builtins.property
    def kind_info(self):
        return next((k for k in KINDS.values() if k.content_type == self.content_type), None)

    @builtins.property
    def is_inline_safe(self):
        return bool(self.kind_info and self.kind_info.inline)

    @builtins.property
    def size_label(self):
        n = float(self.size)
        for unit in ("bytes", "KB", "MB", "GB"):
            if n < 1024 or unit == "GB":
                return f"{int(n)} bytes" if unit == "bytes" else f"{n:.1f} {unit}"
            n /= 1024

    @builtins.property
    def uploader_label(self):
        return self.uploaded_by.email if self.uploaded_by else "—"
