import hashlib
import os
import re
import time
from io import StringIO
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import override_settings

from attachments.models import Attachment
from attachments.services import account_usage, delete_attachment, store_attachment
from core.tenancy import TenantViolation, use_account

from .base import FilesWorld, docx, jpeg, pdf, png, upload


class StoreTests(FilesWorld):
    def test_row_and_file_are_created_with_verified_metadata(self):
        data = pdf("Invoice #4471")
        att = self.store(self.maple, "Boiler invoice 2026.pdf", data, caption="  Replaced igniter  ")
        self.assertEqual((att.original_name, att.content_type, att.size, att.caption, att.uploaded_by, att.account_id),
                         ("Boiler invoice 2026.pdf", "application/pdf", len(data), "Replaced igniter", self.alice, self.acme.pk))
        self.assertEqual(att.sha256, hashlib.sha256(data).hexdigest())
        with open(os.path.join(self.media, att.file.name), "rb") as fh:
            self.assertEqual(fh.read(), data)                                  # bytes on disk are exactly what was uploaded
        self.assertEqual((att.target_kind, att.target, att.target_label), ("property", self.maple, "Maple Duplex"))

    def test_stored_path_is_random_tenant_prefixed_and_never_contains_the_user_filename(self):
        a = self.store(self.maple, "../../etc/secret name.png"); b = self.store(self.maple, "../../etc/secret name.png")
        for att in (a, b):
            self.assertRegex(att.file.name, rf"^attachments/{self.acme.pk}/[0-9a-f]{{32}}\.png$")
            self.assertNotIn("secret", att.file.name)
        self.assertNotEqual(a.file.name, b.file.name)
        self.assertEqual(a.original_name, "secret name.png")

    def test_stored_files_and_folders_are_not_world_readable(self):
        att = self.store(self.maple)
        path = os.path.join(self.media, att.file.name)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o640)
        self.assertEqual(os.stat(os.path.dirname(path)).st_mode & 0o007, 0, "folder must not be accessible to 'other'")

    def test_every_target_type_can_carry_files(self):
        occ = self.occ(self.acme, self.maple, "Pump", self.TODAY)
        for target in (self.maple, self.heater, self.plumber, occ, occ.task, self.event(self.acme, "Inspection", self.TODAY)):
            att = self.store(target)
            self.assertEqual(att.target, target, type(target).__name__)

    def test_unsupported_targets_are_refused(self):
        with use_account(self.acme):
            with self.assertRaises(ValueError):
                store_attachment(target=self.maple_a, uploaded=upload("a.png", png()), user=self.alice)    # Unit has no files

    def test_invalid_uploads_leave_nothing_behind(self):
        with use_account(self.acme):
            with self.assertRaises(ValidationError):
                store_attachment(target=self.maple, uploaded=upload("evil.jpg", b"<html>"), user=self.alice)
        self.assertEqual((self.disk(), Attachment.all_tenants.count()), ([], 0))

    @override_settings(ATTACHMENT_MAX_PER_TARGET=2)
    def test_per_record_file_limit(self):
        self.store(self.maple); self.store(self.maple)
        with self.assertRaisesMessage(ValidationError, "maximum of 2 files"):
            self.store(self.maple)
        self.store(self.oak)                                                    # a different record is unaffected

    @override_settings(ATTACHMENT_ACCOUNT_QUOTA_BYTES=len(png()) * 2 + 10)
    def test_account_quota_counts_only_that_account(self):
        self.store(self.maple); self.store(self.maple)
        with self.assertRaisesMessage(ValidationError, "Storage limit reached"):
            self.store(self.oak)
        self.store(self.lake, account=self.beta, user=self.bella)               # beta has its own allowance
        with use_account(self.acme):
            self.assertEqual(account_usage(), len(png()) * 2)

    def test_failed_insert_removes_the_file_it_just_wrote(self):
        """Fail at the INSERT statement itself, i.e. *after* the file has been written to storage."""
        from django.db.backends.utils import CursorWrapper
        real_execute = CursorWrapper.execute
        written = []

        def execute(cursor, sql, params=None):
            if str(sql).startswith('INSERT INTO "attachments_attachment"'):
                written.extend(self.disk())                                     # the file is already on disk at this point
                raise IntegrityError("boom")
            return real_execute(cursor, sql, params)

        with mock.patch.object(CursorWrapper, "execute", execute):
            with self.assertRaises(IntegrityError):
                self.store(self.maple)
        self.assertEqual(len(written), 1, "the test must really fail after the file was written")
        self.assertEqual(self.disk(), [], "...and the service must have cleaned it up")

    def test_cross_tenant_target_is_refused(self):
        with use_account(self.acme):
            with self.assertRaises(TenantViolation):
                Attachment(property=self.lake, file="x", original_name="x", content_type="x", size=1, sha256="x").save()


class IntegrityTests(FilesWorld):
    def raw(self, **targets):
        with use_account(self.acme):
            Attachment(file="x.bin", original_name="x", content_type="application/pdf", size=1, sha256="x", **targets).save()

    def test_database_enforces_exactly_one_target(self):
        for targets in (dict(), dict(property=self.maple, item=self.heater), dict(property=self.maple, contact=self.plumber)):
            with self.assertRaises(IntegrityError, msg=str(targets)), transaction.atomic():
                self.raw(**targets)
        self.raw(property=self.maple)                                           # exactly one is fine

    def test_targets_cannot_be_hard_deleted_out_from_under_their_files(self):
        self.store(self.maple)
        with self.assertRaises(ProtectedError):
            self.maple.delete()


class DeleteTests(FilesWorld):
    def test_file_is_removed_after_commit_not_before(self):
        att = self.store(self.maple); path = os.path.join(self.media, att.file.name)
        with use_account(self.acme), self.captureOnCommitCallbacks(execute=False) as callbacks:
            delete_attachment(Attachment.objects.get(pk=att.pk))
        self.assertFalse(Attachment.all_tenants.filter(pk=att.pk).exists())
        self.assertTrue(os.path.exists(path), "file must outlive the transaction until it commits")
        for cb in callbacks:
            cb()
        self.assertFalse(os.path.exists(path))

    def test_rollback_keeps_both_row_and_file(self):
        att = self.store(self.maple); path = os.path.join(self.media, att.file.name)
        with use_account(self.acme), self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    delete_attachment(Attachment.objects.get(pk=att.pk))
                    raise RuntimeError("later step failed")
            except RuntimeError:
                pass
        self.assertTrue(os.path.exists(path)); self.assertTrue(Attachment.all_tenants.filter(pk=att.pk).exists())

    def test_a_missing_file_does_not_break_deletion(self):
        att = self.store(self.maple); os.remove(os.path.join(self.media, att.file.name))
        with use_account(self.acme), self.captureOnCommitCallbacks(execute=True):
            delete_attachment(Attachment.objects.get(pk=att.pk))
        self.assertFalse(Attachment.all_tenants.filter(pk=att.pk).exists())


class PurgeOrphansTests(FilesWorld):
    def run_cmd(self, *args):
        out = StringIO(); call_command("purge_orphan_attachments", *args, stdout=out); return out.getvalue()

    def make_orphan(self, name, age_hours):
        folder = os.path.join(self.media, "attachments", "9"); os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        with open(path, "wb") as fh:
            fh.write(b"x")
        old = time.time() - age_hours * 3600
        os.utime(path, (old, old))
        return path

    def test_removes_only_old_unreferenced_files(self):
        keep = self.store(self.maple); keep_path = os.path.join(self.media, keep.file.name)
        old = self.make_orphan("old.png", 48); young = self.make_orphan("young.png", 1)
        dry = self.run_cmd("--dry-run")
        self.assertIn("would remove attachments/9/old.png", dry); self.assertTrue(os.path.exists(old))
        self.run_cmd()
        self.assertEqual((os.path.exists(old), os.path.exists(young), os.path.exists(keep_path)), (False, True, True))

    def test_other_tenants_files_are_recognised_as_referenced(self):
        att = self.store(self.lake, account=self.beta, user=self.bella)
        os.utime(os.path.join(self.media, att.file.name), (0, 0))               # ancient, but referenced
        self.run_cmd(); self.assertTrue(os.path.exists(os.path.join(self.media, att.file.name)))

    def test_no_directory_is_fine(self):
        import shutil
        shutil.rmtree(os.path.join(self.media, "attachments"), ignore_errors=True)
        self.assertIn("Nothing to do", self.run_cmd())
