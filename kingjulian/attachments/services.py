"""Storing and removing attachments. The only code that should write Attachment rows or files."""
import hashlib
import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from .models import Attachment
from .targets import kind_for
from .validators import human_size, inspect_upload, sanitize_filename

log = logging.getLogger(__name__)
DEFAULT_QUOTA = 500 * 1024 * 1024
DEFAULT_MAX_PER_TARGET = 50


def account_usage() -> int:
    """Bytes stored by the active account."""
    return Attachment.objects.aggregate(total=Sum("size"))["total"] or 0


@transaction.atomic
def store_attachment(*, target, uploaded, user, caption=""):
    kind = kind_for(target)
    if kind is None:
        raise ValueError(f"{type(target).__name__} cannot carry attachments")
    found = inspect_upload(uploaded)                                   # raises ValidationError

    field = {"item": "item", "task": "task", "occurrence": "occurrence", "contact": "contact",
             "event": "event", "property": "property"}[kind]
    per_target = getattr(settings, "ATTACHMENT_MAX_PER_TARGET", DEFAULT_MAX_PER_TARGET)
    if Attachment.objects.filter(**{field: target}).count() >= per_target:
        raise ValidationError(f"This record already has the maximum of {per_target} files.")
    quota = getattr(settings, "ATTACHMENT_ACCOUNT_QUOTA_BYTES", DEFAULT_QUOTA)
    if account_usage() + uploaded.size > quota:
        raise ValidationError(f"Storage limit reached ({human_size(quota)} for this account).")

    digest = hashlib.sha256()
    for chunk in uploaded.chunks():
        digest.update(chunk)
    uploaded.seek(0)

    att = Attachment(account_id=target.account_id, **{field: target}, file=uploaded,
                     original_name=sanitize_filename(uploaded.name), content_type=found.content_type,
                     size=uploaded.size, sha256=digest.hexdigest(), caption=caption.strip()[:200], uploaded_by=user)
    att.stored_ext = found.ext
    try:
        att.save()                                                     # writes the file, then the row
    except Exception:
        if att.file and att.file.name:                                 # don't leave an orphan if the insert failed
            att.file.storage.delete(att.file.name)
        raise
    return att


def delete_attachment(att: Attachment) -> None:
    """Remove the row now and the file once the transaction commits (so a rollback can't lose the file)."""
    storage, name = att.file.storage, att.file.name
    att.delete()

    def _remove():
        try:
            storage.delete(name)
        except Exception:                                              # noqa: BLE001
            log.exception("could not delete attachment file %s", name)

    transaction.on_commit(_remove)
