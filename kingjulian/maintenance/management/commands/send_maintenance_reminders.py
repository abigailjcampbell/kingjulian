"""Daily job: python manage.py send_maintenance_reminders   (run from cron once a day)."""
from collections import Counter
from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Account
from core.tenancy import use_account
from maintenance import reminders


class Command(BaseCommand):
    help = "Generate and send maintenance reminders for every account."

    def add_arguments(self, parser):
        parser.add_argument("--today", type=date.fromisoformat,
                            help="Pretend today is YYYY-MM-DD for every account (testing). Default: each account's own local date.")
        parser.add_argument("--dry-run", action="store_true", help="Report what would be sent; change nothing.")

    def handle(self, *args, today=None, dry_run=False, **opts):
        total = Counter()
        accounts = 0
        with transaction.atomic():
            for account in Account.objects.order_by("id"):
                day = today or account.today()           # "today" depends on where the account is
                with use_account(account):
                    gen = reminders.generate_reminders(day)
                    sent = reminders.send_pending(day, dry_run=dry_run)
                total.update(gen); total.update(sent); accounts += 1
                self.stdout.write(f"{account.name} [{account.timezone}, {day}]: {dict(gen) | dict(sent) or 'nothing to do'}")
            if dry_run:
                transaction.set_rollback(True)     # generation writes rows; discard them
        label = "DRY RUN " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(f"{label}{accounts} account(s): {dict(total) or 'nothing to do'}"))
