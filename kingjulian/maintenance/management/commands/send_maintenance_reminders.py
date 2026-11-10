"""Daily job: python manage.py send_maintenance_reminders   (run from cron once a day)."""
from collections import Counter
from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import Account
from core.tenancy import use_account
from maintenance import reminders


class Command(BaseCommand):
    help = "Generate and send maintenance reminders for every account."

    def add_arguments(self, parser):
        parser.add_argument("--today", type=date.fromisoformat, help="Pretend today is YYYY-MM-DD (testing).")
        parser.add_argument("--dry-run", action="store_true", help="Report what would be sent; change nothing.")

    def handle(self, *args, today=None, dry_run=False, **opts):
        today = today or timezone.localdate()
        total = Counter()
        with transaction.atomic():
            for account in Account.objects.order_by("id"):
                with use_account(account):
                    gen = reminders.generate_reminders(today)
                    sent = reminders.send_pending(today, dry_run=dry_run)
                total.update(gen); total.update(sent)
                self.stdout.write(f"{account.name}: {dict(gen) | dict(sent) or 'nothing to do'}")
            if dry_run:
                transaction.set_rollback(True)     # generation writes rows; discard them
        label = "DRY RUN " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(f"{label}{today}: {dict(total) or 'nothing to do'}"))
