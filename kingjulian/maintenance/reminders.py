"""Daily reminder job: generate planned reminders, then send the ones that are due.

Both steps are idempotent, so running the job twice in a day (or after downtime) never double-sends:
  * generation relies on the (occurrence, kind, remind_on) unique key
  * sending claims each row with SELECT ... FOR UPDATE SKIP LOCKED and only sends PENDING rows

Delivery is at-least-once: if the process dies between the SMTP send and the commit, that one
reminder is re-sent on the next run. Run inside core.tenancy.use_account(account).
"""
import logging
from collections import Counter
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from accounts.models import Membership, User

from .models import Reminder, TaskOccurrence

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
Kind, Status = Reminder.Kind, Reminder.Status


def recipients_for(account_id) -> list[str]:
    """Owners and managers of the account (viewers are never emailed)."""
    return sorted(set(
        User.objects.filter(is_active=True, memberships__account_id=account_id,
                            memberships__role__in=[Membership.Role.OWNER, Membership.Role.MANAGER])
        .exclude(email="").values_list("email", flat=True)))


def _supersede_pending(occ, *, before=None):
    qs = Reminder.objects.filter(occurrence=occ, status=Status.PENDING, kind=Kind.UPCOMING)
    if before is not None:
        qs = qs.filter(remind_on__lt=before)
    return qs.update(status=Status.SUPERSEDED)


def generate_reminders(today) -> Counter:
    stats = Counter()
    open_occurrences = (TaskOccurrence.objects
                        .filter(status=TaskOccurrence.Status.OPEN, task__archived_at__isnull=True)
                        .select_related("task"))
    for occ in open_occurrences:
        if occ.due_date < today:
            _supersede_pending(occ)
            _, created = Reminder.objects.get_or_create(
                occurrence=occ, kind=Kind.OVERDUE, remind_on=occ.due_date + timedelta(days=1),
                defaults={"account_id": occ.account_id})
            stats["created"] += created
            continue
        slots = sorted(d for d in {occ.due_date - timedelta(days=n) for n in occ.task.remind_days_before}
                       if d <= today)
        if not slots:
            continue
        latest = slots[-1]
        for day in slots:
            _, created = Reminder.objects.get_or_create(
                occurrence=occ, kind=Kind.UPCOMING, remind_on=day, defaults={"account_id": occ.account_id})
            stats["created"] += created
        # If the job was down (or an earlier send failed), several slots can be live at once:
        # only the newest is actually sent, older unsent ones are retired.
        stats["superseded"] += _supersede_pending(occ, before=latest)
    return stats


def _phrase(due, today):
    days = (due - today).days
    if days < 0:
        return f"is {-days} day{'s' if days != -1 else ''} overdue"
    if days == 0:
        return "is due today"
    return f"is due in {days} day{'s' if days != 1 else ''}"


def build_email(reminder, today, bcc):
    occ = reminder.occurrence
    task = occ.task
    where = " / ".join(filter(None, [task.property.name, task.unit.name if task.unit else "",
                                     task.item.name if task.item else ""]))
    link = f"{settings.SITE_URL}{reverse('occurrence_edit', args=[occ.pk])}"
    lines = [f"{task.title} {_phrase(occ.due_date, today)} ({occ.due_date:%A, %b %d, %Y}).", "",
             f"Where: {where}", f"Repeats: {task.schedule_label}"]
    if task.assigned_contact:
        c = task.assigned_contact
        lines.append("Assigned to: " + ", ".join(filter(None, [c.name, c.phone, c.email])))
    if task.description:
        lines += ["", task.description]
    lines += ["", f"Mark it done or skip it: {link}"]
    subject = f"Maintenance {_phrase(occ.due_date, today).replace('is ', '', 1)}: {task.title} ({task.property.name})"
    return EmailMessage(subject=subject, body="\n".join(lines), bcc=bcc)


def send_pending(today, *, dry_run=False) -> Counter:
    stats = Counter()
    ids = list(Reminder.objects.filter(status=Status.PENDING, remind_on__lte=today).values_list("pk", flat=True))
    recipients = None
    for pk in ids:
        with transaction.atomic():
            r = (Reminder.objects.select_for_update(skip_locked=True, of=("self",))
                 .filter(pk=pk, status=Status.PENDING)
                 .select_related("occurrence__task__property", "occurrence__task__unit",
                                 "occurrence__task__item", "occurrence__task__assigned_contact")
                 .first())
            if r is None:                              # another worker has it, or it was already sent
                stats["skipped_locked"] += 1
                continue
            occ = r.occurrence
            if occ.status != TaskOccurrence.Status.OPEN or occ.task.is_archived:
                if not dry_run:
                    r.status = Status.SUPERSEDED
                    r.save(update_fields=["status", "updated_at"])
                stats["superseded"] += 1
                continue
            if recipients is None:
                recipients = recipients_for(r.account_id)
            if dry_run:
                stats["would_send"] += 1
                continue
            if not recipients:
                r.status = Status.NO_RECIPIENTS
                r.save(update_fields=["status", "updated_at"])
                stats["no_recipients"] += 1
                continue
            r.attempts += 1
            try:
                build_email(r, today, recipients).send(fail_silently=False)
            except Exception as exc:                  # noqa: BLE001 - any transport error is retryable
                log.warning("reminder %s failed (attempt %s): %s", r.pk, r.attempts, exc)
                r.last_error = str(exc)[:500]
                r.status = Status.FAILED if r.attempts >= MAX_ATTEMPTS else Status.PENDING
                r.save(update_fields=["attempts", "last_error", "status", "updated_at"])
                stats["failed"] += 1
                continue
            r.status, r.sent_at, r.recipient_count = Status.SENT, timezone.now(), len(recipients)
            r.save(update_fields=["attempts", "status", "sent_at", "recipient_count", "updated_at"])
            stats["sent"] += 1
    return stats
