import os
from datetime import date as D, timedelta
from decimal import Decimal
from unittest import mock

from django.db.models import ProtectedError
from django.test import Client
from django.utils import timezone

from accounts.models import Membership, User
from archive.deletion import CONFIRM_WORD, DeleteBlocked, NotArchived, build_plan, delete_forever, plural
from attachments.models import Attachment
from attachments.tests.base import pdf, png
from audit.context import acting_as
from audit.models import ActivityLog
from contacts.models import Contact, ContactProperty
from core.tenancy import use_account
from core.tests.base import PW
from inventory.models import InventoryItem
from maintenance.models import MaintenanceTask, Reminder, TaskOccurrence
from maintenance.reminders import generate_reminders
from maintenance.services import complete_occurrence, ensure_first_occurrence
from planner.models import Event
from properties.models import Property, Unit

from .base import ArchiveWorld

XSS = "<script>alert(1)</script>"


class DeleteWorld(ArchiveWorld):
    def setUp(self):
        ActivityLog.all_tenants.all().delete()
        self.today = timezone.localdate()

    # ---- realistic, interlocked data ---------------------------------------------------------------------
    def contact_with_everything(self):
        with use_account(self.acme):
            c = Contact.objects.create(name="Zed Roofer", role="contractor", company="Roofers Inc")
            ContactProperty.objects.create(contact=c, property=self.maple); ContactProperty.objects.create(contact=c, property=self.oak)
            t = MaintenanceTask.objects.create(property=self.maple, title="Roof check", start_date=self.today, recurrence_rule="", assigned_contact=c)
            ev = Event.objects.create(title="Roof walkthrough", starts_on=self.today, contact=c)
        self.store(c, "COI.pdf", pdf("certificate")); self.store(c, "license.png", png())
        return c, t, ev

    def task_with_history(self, title="Gutter cleaning"):
        with use_account(self.acme):
            t = MaintenanceTask.objects.create(property=self.oak, title=title, start_date=self.today - timedelta(days=60),
                                               recurrence_rule="FREQ=MONTHLY", remind_days_before=[1, 0])
            ensure_first_occurrence(t)
            for cost in ("85.50", "640.00"):
                complete_occurrence(TaskOccurrence.objects.get(task=t, status="open"), completed_on=self.today - timedelta(days=30),
                                    cost=Decimal(cost))
            generate_reminders(TaskOccurrence.objects.get(task=t, status="open").due_date)     # the day the open job falls due
        with use_account(self.acme):
            occ = TaskOccurrence.objects.filter(task=t).order_by("id").first()
        self.store(t, "task-manual.pdf", pdf("how-to")); self.store(occ, "receipt.png", png())
        return t

    def plan(self, obj):
        with use_account(self.acme):
            return build_plan(obj)

    def archived(self, obj):
        self.archive(obj, user=self.alice)
        return self.fresh(obj)

    def stored_paths(self):
        return {a.file.name for a in Attachment.all_tenants.all()}

    def on_disk(self, name):
        return os.path.exists(os.path.join(self.media, name))


class PlanTests(DeleteWorld):
    def test_pluralisation(self):
        self.assertEqual([plural(1, "file"), plural(2, "file"), plural(0, "job")], ["1 file", "2 files", "0 jobs"])

    def test_contact_plan_lists_files_and_what_will_merely_change(self):
        c, t, ev = self.contact_with_everything()
        plan = self.plan(self.archived(c))
        self.assertEqual(plan.blockers, [])
        self.assertTrue(plan.deletes[0].startswith("2 files ("), plan.deletes)
        self.assertEqual(plan.file_names, ["COI.pdf", "license.png"])
        self.assertEqual(plan.unlinks, ["1 maintenance task will keep existing but no longer have this contact assigned",
                                        "1 event will keep existing but no longer list this contact", "2 contact-to-property links will be removed"])

    def test_task_plan_counts_history_cost_reminders_and_every_file(self):
        t = self.task_with_history()
        plan = self.plan(self.archived(t))
        self.assertIn("3 jobs of history (2 completed, $725.50 of recorded cost)", plan.deletes)
        self.assertTrue(any("reminder record" in d for d in plan.deletes), plan.deletes)
        self.assertEqual(sorted(plan.file_names), ["receipt.png", "task-manual.pdf"])         # one on the task, one on a job
        self.assertTrue(plan.deletes[0].startswith("2 files"))

    def test_a_task_with_no_history_says_so_plainly(self):
        with use_account(self.acme):
            t = MaintenanceTask.objects.create(property=self.oak, title="Empty", start_date=self.today, recurrence_rule="")
        plan = self.plan(self.archived(t))
        self.assertEqual((plan.deletes, plan.blockers), ([], []))

    def test_property_is_blocked_by_everything_that_belongs_to_it(self):
        objs = self.make_all()
        plan = self.plan(self.archived(objs["property"]))
        found = {b.label: b.count for b in plan.blockers}
        self.assertEqual(found, {"unit": 1, "inventory item": 1, "maintenance task": 1, "event": 1})
        links = {b.label: dict(b.examples) for b in plan.blockers}
        self.assertEqual(links["unit"]["Cellar"], f"/properties/units/{objs['unit'].pk}/")               # active: its live page
        self.archive(objs["unit"])
        links = {b.label: dict(b.examples) for b in self.plan(self.fresh(objs["property"])).blockers}
        self.assertEqual(links["unit"]["Cellar"], f"/archive/unit/{objs['unit'].pk}/")                   # archived: its archive page

    def test_units_and_items_are_blocked_by_the_things_inside_or_using_them(self):
        objs = self.make_all()
        self.assertEqual({b.label for b in self.plan(self.archived(objs["unit"])).blockers}, {"inventory item"})
        with use_account(self.acme):
            t = objs["task"]; t.item = objs["item"]; t.save()
        self.assertEqual({b.label for b in self.plan(self.archived(objs["item"])).blockers}, {"maintenance task"})

    def test_no_more_than_five_examples_are_listed_but_the_count_is_exact(self):
        with use_account(self.acme):
            p = Property.objects.create(name="Big")
            for i in range(8):
                Unit.objects.create(property=p, name=f"U{i}")
        b, = self.plan(self.archived(p)).blockers
        self.assertEqual((b.count, len(b.examples)), (8, 5))

    def test_the_token_changes_when_what_would_be_deleted_changes(self):
        c, t, ev = self.contact_with_everything()
        c = self.archived(c)
        same = (self.plan(c).token, self.plan(c).token)
        self.assertEqual(same[0], same[1])
        with use_account(self.acme):
            Event.objects.create(title="Another", starts_on=self.today, contact=c)      # one more thing the plan has to mention
        self.assertNotEqual(self.plan(c).token, same[0])


class ExecutionTests(DeleteWorld):
    def test_only_archived_records_can_be_deleted(self):
        c, t, ev = self.contact_with_everything()
        with use_account(self.acme):
            with self.assertRaises(NotArchived):
                delete_forever(Contact.objects.get(pk=c.pk))
        self.assertTrue(Contact.all_tenants.filter(pk=c.pk).exists())

    def test_deleting_a_contact_takes_its_files_and_links_but_keeps_the_work(self):
        c, t, ev = self.contact_with_everything()
        c = self.archived(c)
        paths = self.stored_paths(); self.assertEqual(len(paths), 2)
        with use_account(self.acme):
            other = Contact.objects.create(name="Zed Roofer")                          # same name, still active: must survive
        with use_account(self.acme), acting_as(self.alice), self.captureOnCommitCallbacks(execute=True):
            delete_forever(Contact.objects.get(pk=c.pk))
        self.assertFalse(Contact.all_tenants.filter(pk=c.pk).exists()); self.assertTrue(Contact.all_tenants.filter(pk=other.pk).exists())
        self.assertEqual((Attachment.all_tenants.count(), ContactProperty.all_tenants.count()), (0, 0))
        self.assertFalse(any(self.on_disk(p) for p in paths), "files must leave the disk once committed")
        self.assertIsNone(MaintenanceTask.all_tenants.get(pk=t.pk).assigned_contact_id)          # the work stays, unassigned
        self.assertIsNone(Event.all_tenants.get(pk=ev.pk).contact_id)

    def test_one_summary_entry_replaces_the_per_row_noise(self):
        c, t, ev = self.contact_with_everything()
        c = self.archived(c)
        ActivityLog.all_tenants.all().delete()
        with use_account(self.acme), acting_as(self.alice), self.captureOnCommitCallbacks(execute=True):
            delete_forever(Contact.objects.get(pk=c.pk))
        with use_account(self.acme):
            e, = ActivityLog.objects.all()                                              # no "Deleted file", no "Unlinked ..." entries
        self.assertEqual((e.action, e.summary, e.actor, e.target_type, e.target_id), ("deleted", "Permanently deleted contact “Zed Roofer”", self.alice, "contact", c.pk))
        self.assertEqual(e.changes["Files"], ["", "COI.pdf, license.png"]); self.assertIn("2 files", e.changes["Also deleted"][1])
        self.assertIn("1 maintenance task", e.changes["Also changed"][1])

    def test_deleting_a_task_takes_its_whole_history_reminders_and_files(self):
        t = self.task_with_history(); keep = self.task_with_history("Keep me")
        t = self.archived(t); paths = self.stored_paths(); self.assertEqual(len(paths), 4)
        with use_account(self.acme):
            before_keep = TaskOccurrence.objects.filter(task=keep).count(), Reminder.objects.filter(occurrence__task=keep).count()
        with use_account(self.acme), acting_as(self.alice), self.captureOnCommitCallbacks(execute=True):
            delete_forever(MaintenanceTask.objects.get(pk=t.pk))
        self.assertEqual((TaskOccurrence.all_tenants.filter(task_id=t.pk).count(), Reminder.all_tenants.filter(occurrence__task_id=t.pk).count()), (0, 0))
        self.assertEqual(sorted(a.original_name for a in Attachment.all_tenants.all()), ["receipt.png", "task-manual.pdf"])      # only Keep me's files remain
        with use_account(self.acme):
            self.assertEqual((TaskOccurrence.objects.filter(task=keep).count(), Reminder.objects.filter(occurrence__task=keep).count()), before_keep)
        self.assertEqual(len([p for p in paths if self.on_disk(p)]), 2)
        with use_account(self.acme):
            e = ActivityLog.objects.get(action="deleted")
        self.assertIn("3 jobs of history (2 completed, $725.50", e.changes["Also deleted"][1]); self.assertEqual(ActivityLog.all_tenants.filter(action="deleted").count(), 1)

    def test_a_property_cannot_be_deleted_until_its_contents_are_gone(self):
        objs = self.make_all(); prop = self.archived(objs["property"])
        self.store(prop, "deed.pdf", pdf("deed")); paths = self.stored_paths()
        with use_account(self.acme):
            with self.assertRaises(DeleteBlocked) as ctx:
                delete_forever(Property.objects.get(pk=prop.pk))
        self.assertEqual({b.label for b in ctx.exception.plan.blockers}, {"unit", "inventory item", "maintenance task", "event"})
        self.assertTrue(Property.all_tenants.filter(pk=prop.pk).exists()); self.assertTrue(all(self.on_disk(p) for p in paths))
        for key in ("item", "unit", "task", "event"):                                    # clear the contents: items before units
            with use_account(self.acme), self.captureOnCommitCallbacks(execute=True):
                delete_forever(type(objs[key]).objects.get(pk=objs[key].pk)) if self.fresh(objs[key]).archived_at else (self.archive(objs[key]), delete_forever(self.fresh(objs[key])))
        with use_account(self.acme), self.captureOnCommitCallbacks(execute=True):
            delete_forever(Property.objects.get(pk=prop.pk))
        self.assertFalse(Property.all_tenants.filter(pk=prop.pk).exists()); self.assertEqual(Attachment.all_tenants.count(), 0)
        self.assertFalse(any(self.on_disk(p) for p in paths))

    def test_contacts_linked_to_a_deleted_property_remain(self):
        with use_account(self.acme):
            p = Property.objects.create(name="Solo"); c = Contact.objects.create(name="Linked"); ContactProperty.objects.create(contact=c, property=p)
        self.archive(p)
        with use_account(self.acme):
            delete_forever(Property.objects.get(pk=p.pk))
        self.assertTrue(Contact.all_tenants.filter(pk=c.pk).exists()); self.assertEqual(ContactProperty.all_tenants.count(), 0)

    def test_event_and_item_deletion_take_their_files(self):
        with use_account(self.acme):
            ev = Event.objects.create(title="Inspection", starts_on=self.today); it = InventoryItem.objects.create(property=self.oak, name="Stove")
        self.store(ev, "report.pdf", pdf("r")); self.store(it, "manual.pdf", pdf("m")); self.archive(ev); self.archive(it)
        with use_account(self.acme), self.captureOnCommitCallbacks(execute=True):
            delete_forever(Event.objects.get(pk=ev.pk)); delete_forever(InventoryItem.objects.get(pk=it.pk))
        self.assertEqual((Attachment.all_tenants.count(), self.disk()), (0, []))

    def test_a_failure_part_way_rolls_everything_back_including_the_files(self):
        c, t, ev = self.contact_with_everything(); c = self.archived(c)
        paths = self.stored_paths(); ActivityLog.all_tenants.all().delete()
        with use_account(self.acme), self.captureOnCommitCallbacks(execute=True):
            with mock.patch.object(Contact, "delete", side_effect=RuntimeError("disk on fire")):
                with self.assertRaises(RuntimeError):
                    delete_forever(Contact.objects.get(pk=c.pk))
        self.assertTrue(Contact.all_tenants.filter(pk=c.pk).exists()); self.assertEqual(Attachment.all_tenants.count(), 2)
        self.assertTrue(all(self.on_disk(p) for p in paths), "a rolled-back delete must not have removed any file")
        self.assertEqual(ActivityLog.all_tenants.count(), 0)

    def test_files_only_leave_the_disk_once_the_transaction_commits(self):
        c, t, ev = self.contact_with_everything(); c = self.archived(c); paths = self.stored_paths()
        with use_account(self.acme), self.captureOnCommitCallbacks(execute=False) as callbacks:
            delete_forever(Contact.objects.get(pk=c.pk))
            self.assertTrue(all(self.on_disk(p) for p in paths))
        for cb in callbacks:
            cb()
        self.assertFalse(any(self.on_disk(p) for p in paths))

    def test_normal_auditing_resumes_after_a_deletion(self):
        with use_account(self.acme):
            p = Property.objects.create(name="Temp"); p.archive(); delete_forever(Property.objects.get(pk=p.pk))
            Property.objects.create(name="After")
        self.assertIn("Created property “After”", [e.summary for e in ActivityLog.all_tenants.all()])

    def test_the_database_itself_refuses_a_careless_delete(self):
        t = self.task_with_history()
        with use_account(self.acme):
            with self.assertRaises(ProtectedError):
                MaintenanceTask.objects.get(pk=t.pk).delete()                           # second line of defence under our own rules
        self.assertTrue(MaintenanceTask.all_tenants.filter(pk=t.pk).exists())

    def test_other_accounts_are_never_touched(self):
        with use_account(self.beta):
            theirs = Contact.objects.create(name="Zed Roofer"); theirs.archive()
        theirs_file = self.store(theirs, "theirs.png", png(), account=self.beta, user=self.bella)
        c, t, ev = self.contact_with_everything(); c = self.archived(c)
        with use_account(self.acme), self.captureOnCommitCallbacks(execute=True):
            delete_forever(Contact.objects.get(pk=c.pk))
        self.assertTrue(Contact.all_tenants.filter(pk=theirs.pk).exists())
        self.assertTrue(Attachment.all_tenants.filter(pk=theirs_file.pk).exists()); self.assertTrue(self.on_disk(theirs_file.file.name))
