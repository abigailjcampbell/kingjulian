"""Signal handlers that turn saves and deletes into ActivityLog rows (in the same transaction)."""
from django.db.models.signals import post_delete, post_save, pre_save

from contacts.models import ContactProperty
from maintenance.models import TaskOccurrence

from .context import get_actor
from .models import ActivityLog
from .registry import SPECS, describe_target, display, tracked_fields, verbose

A = ActivityLog.Action


def record(*, account_id, action, summary, target=("", None, ""), changes=None):
    actor = get_actor()
    ActivityLog(account_id=account_id, actor=actor, actor_email=getattr(actor, "email", "") or "", action=action,
                target_type=target[0], target_id=target[1], target_label=target[2][:200],
                summary=summary[:400], changes=changes or {}).save()


def _on_pre_save(sender, instance, raw=False, **kwargs):
    """Remember what the row looked like before this save (one SELECT) so post_save can diff it."""
    spec = SPECS.get(sender)
    if raw or spec is None or instance._state.adding or instance.pk is None:
        instance._audit_old = None
        return
    attnames = [f.attname for f in tracked_fields(spec)] + ["archived_at"]
    row = sender.all_tenants.filter(pk=instance.pk).values(*attnames).first()
    instance._audit_old = {"tracked": row, "archived_at": row["archived_at"] if row else None}


def _diff(spec, instance, old_tracked, update_fields):
    changes = {}
    for f in tracked_fields(spec):
        if update_fields is not None and f.name not in update_fields and f.attname not in update_fields:
            continue                       # not actually written, so not a change
        before, after = old_tracked.get(f.attname), getattr(instance, f.attname)
        if before != after and not (before in (None, "") and after in (None, "")):
            changes[str(f.verbose_name).capitalize()] = [display(f, before), display(f, after)]
    return changes


def _on_post_save(sender, instance, created, raw=False, update_fields=None, **kwargs):
    spec = SPECS.get(sender)
    if raw or spec is None:
        return
    target = describe_target(instance)
    kind, label = verbose(instance), target[2]
    if created:
        if spec.track_create:
            verb = {"created": "Created", "uploaded": "Uploaded", "linked": "Linked"}[spec.create_action]
            if isinstance(instance, ContactProperty):
                summary = f"Linked contact “{label}” to property “{instance.property.name}”"
            elif spec.create_action == "uploaded":
                summary = f"Uploaded file “{instance.original_name}” to {instance.target_type_label.lower()} “{label}”"
            else:
                summary = f"{verb} {kind} “{label}”"
            record(account_id=instance.account_id, action=spec.create_action, summary=summary, target=target)
        return
    old = getattr(instance, "_audit_old", None)
    if not old or old["tracked"] is None:
        return
    changes = _diff(spec, instance, old["tracked"], update_fields)

    was_archived, now_archived = old["archived_at"] is not None, instance.archived_at is not None
    if was_archived != now_archived:
        action, verb = (A.ARCHIVED, "Archived") if now_archived else (A.RESTORED, "Restored")
        record(account_id=instance.account_id, action=action, summary=f"{verb} {kind} “{label}”", target=target)

    if not changes:
        return
    if isinstance(instance, TaskOccurrence) and "Status" in changes:
        new_status = instance.status
        action, verb = {"done": (A.COMPLETED, "Completed"), "skipped": (A.SKIPPED, "Skipped")}.get(new_status, (A.REOPENED, "Reopened"))
        summary = f"{verb} {kind} “{label}” (due {instance.due_date:%b %-d, %Y})"
    else:
        action, verb = A.UPDATED, "Updated"
        shown = ", ".join(list(changes)[:4]) + ("…" if len(changes) > 4 else "")
        summary = f"Updated {kind} “{label}” ({shown})"
    record(account_id=instance.account_id, action=action, summary=summary, target=target, changes=changes)


def _on_post_delete(sender, instance, **kwargs):
    spec = SPECS.get(sender)
    if spec is None:
        return
    try:
        target = describe_target(instance)
    except Exception:                                  # the parent may be gone too (cascades); log what we can
        target = (sender._meta.model_name, instance.pk, str(getattr(instance, "original_name", "") or instance.pk))
    if isinstance(instance, ContactProperty):
        summary = f"Unlinked contact “{target[2]}” from property “{instance.property.name}”"
    elif spec.delete_action == "deleted" and hasattr(instance, "original_name"):
        summary = f"Deleted file “{instance.original_name}” from {instance.target_type_label.lower()} “{target[2]}”"
    else:
        summary = f"Deleted {verbose(instance)} “{target[2]}”"
    record(account_id=instance.account_id, action=spec.delete_action, summary=summary, target=target)



def connect():
    for model in SPECS:
        uid = f"audit-{model._meta.label_lower}"
        pre_save.connect(_on_pre_save, sender=model, dispatch_uid=f"{uid}-pre")
        post_save.connect(_on_post_save, sender=model, dispatch_uid=f"{uid}-post")
        post_delete.connect(_on_post_delete, sender=model, dispatch_uid=f"{uid}-del")
