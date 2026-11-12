"""Delete files under MEDIA_ROOT/attachments/ that no Attachment row points at (left by a failed upload or a crash).

python manage.py purge_orphan_attachments --dry-run       # report only
python manage.py purge_orphan_attachments                  # delete files older than 24 hours
"""
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from attachments.models import Attachment


class Command(BaseCommand):
    help = "Remove stored files that no attachment row references."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--min-age-hours", type=float, default=24.0,
                            help="Skip files newer than this, so an upload in progress is never touched.")

    def handle(self, *args, dry_run=False, min_age_hours=24.0, **opts):
        root = Path(settings.MEDIA_ROOT) / "attachments"
        if not root.is_dir():
            self.stdout.write("Nothing to do: no attachments directory.")
            return
        known = set(Attachment.all_tenants.values_list("file", flat=True))
        cutoff = time.time() - min_age_hours * 3600
        removed = kept_young = 0
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            rel = path.relative_to(settings.MEDIA_ROOT).as_posix()
            if rel in known:
                continue
            if path.stat().st_mtime > cutoff:
                kept_young += 1
                continue
            self.stdout.write(f"{'would remove' if dry_run else 'removing'} {rel}")
            if not dry_run:
                path.unlink()
            removed += 1
        self.stdout.write(self.style.SUCCESS(
            f"{'Would remove' if dry_run else 'Removed'} {removed} orphan file(s); {kept_young} too new to touch."))
