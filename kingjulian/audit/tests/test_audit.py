from datetime import date as D
from decimal import Decimal
from unittest import mock

from django.contrib import admin
from django.db import transaction
from django.test import RequestFactory

from audit.admin import ActivityLogAdmin
from audit.context import acting_as
from audit.models import ActivityLog, ImmutableLog
from audit.registry import display
from contacts.models import Contact
from core.tenancy import use_account
from inventory.models import InventoryItem
from maintenance.models import TaskOccurrence
from maintenance.services import complete_occurrence, skip_occurrence
from planner.models import Event
from properties.models import Property, Unit

from attachments.tests.base import png
from .base import AuditWorld


class RecordingTests(AuditWorld):
    def test_lifecycle_of_a_record(self):
        with use_account(self.acme), acting_as(self.alice):
            p = Property.objects.create(name="Birch Lodge", city="Ogdenville")
            p.city = "Springfield"; p.save()
            p.archive(); p.restore()
        entries = self.log()
        self.assertEqual([e.action for e in entries], ["created", "updated", "archived", "restored"])
        self.assertEqual([e.summary for e in entries], ["Created property “Birch Lodge”", "Updated property “Birch Lodge” (City)",
                                                        "Archived property “Birch Lodge”", "Restored property “Birch Lodge”"])
        self.assertEqual(entries[1].changes, {"City": ["Ogdenville", "Springfield"]})
        self.assertEqual({(e.actor, e.actor_email, e.target_type, e.target_id, e.target_label) for e in entries},
                         {(self.alice, "alice@acme.test", "property", p.pk, "Birch Lodge")})

    def test_saving_without_changes_logs_nothing(self):
        with use_account(self.acme), acting_as(self.alice):
            p = Property.objects.get(pk=self.maple.pk); p.save(); p.save()
        self.assertEqual(self.log(), [])

    def test_partial_saves_only_report_the_fields_actually_written(self):
        with use_account(self.acme), acting_as(self.alice):
            p = Property.objects.get(pk=self.maple.pk)
            p.city = "Shelbyville"; p.name = "NOT SAVED"
            p.save(update_fields=["city"])
        self.assertEqual(self.log()[0].changes, {"City": ["Springfield", "Shelbyville"]})
        self.assertEqual(Property.all_tenants.get(pk=self.maple.pk).name, "Maple Duplex")

    def test_actions_outside_a_request_are_attributed_to_the_system(self):
        with use_account(self.acme):
            Property.objects.create(name="Cron-made")
        e = self.log()[0]
        self.assertEqual((e.actor, e.actor_email, e.actor_label), (None, "", "System"))

    def test_values_are_rendered_for_people_not_databases(self):
        with use_account(self.acme), acting_as(self.alice):
            it = InventoryItem.objects.get(pk=self.heater.pk)
            it.condition, it.cost, it.warranty_expires_on, it.quantity = "poor", Decimal("640"), D(2026, 12, 1), 2
            it.property, it.unit = self.oak, None                                      # FK changes show names
            it.notes = "x" * 500
            it.save()
        changes = self.log()[0].changes
        self.assertEqual(changes["Condition"], ["Good", "Poor"]); self.assertEqual(changes["Cost"], ["", "640.00"])
        self.assertEqual(changes["Warranty expires on"], ["", "2026-12-01"]); self.assertEqual(changes["Quantity"], ["1", "2"])
        self.assertEqual(changes["Property"], ["Maple Duplex", "Oak Cottage"]); self.assertEqual(changes["Unit"], ["Maple Duplex / Unit A", ""])
        self.assertEqual(len(changes["Notes"][1]), 200); self.assertTrue(changes["Notes"][1].endswith("…"))
        self.assertIn("Unit: Maple Duplex / Unit A → —", self.log()[0].changes_label)

    def test_json_fields_are_readable(self):
        task = self.occ(self.acme, self.maple, "Pump", self.TODAY).task
        ActivityLog.all_tenants.all().delete()
        with use_account(self.acme):
            task.remind_days_before = [3]; task.save()
        self.assertEqual(self.log()[0].changes, {"Remind days before": ["[7, 1]", "[3]"]})

    def test_every_audited_model_logs_creation_except_scheduler_made_occurrences(self):
        with use_account(self.acme):
            Unit.objects.create(property=self.oak, name="Garage"); Contact.objects.create(name="Dana2")
            Event.objects.create(title="Inspection", starts_on=D(2026, 12, 1))
            InventoryItem.objects.create(property=self.oak, name="Stove")
            self.occ(self.acme, self.oak, "Gutters", D(2026, 12, 1))
        self.assertEqual(sorted(e.target_type for e in self.log(action="created")),
                         ["contact", "event", "inventoryitem", "maintenancetask", "unit"])

    def test_hard_deletes_are_recorded(self):
        with use_account(self.acme), acting_as(self.alice):
            unit = Unit.objects.create(property=self.oak, name="Shed"); unit.delete()
        self.assertEqual(self.actions(), ["created", "deleted"]); self.assertEqual(self.log()[-1].summary, "Deleted unit “Oak Cottage / Shed”")


class OccurrenceTests(AuditWorld):
    def setUp(self):
        super().setUp()
        self.o = self.occ(self.acme, self.maple, "Pump test", D(2026, 11, 15))
        ActivityLog.all_tenants.all().delete()

    def test_completing_is_one_readable_entry_and_the_next_occurrence_is_silent(self):
        with use_account(self.acme), acting_as(self.alice):
            self.o.task.recurrence_rule = "FREQ=MONTHLY"; self.o.task.save()
            ActivityLog.all_tenants.all().delete()
            complete_occurrence(TaskOccurrence.objects.get(pk=self.o.pk), completed_on=D(2026, 11, 14), user=self.alice,
                                cost=Decimal("85"), notes="Replaced float switch")
        e, = self.log()
        self.assertEqual((e.action, e.summary), ("completed", "Completed maintenance job “Pump test” (due Nov 15, 2026)"))
        self.assertEqual(e.changes["Status"], ["Open", "Done"]); self.assertEqual(e.changes["Cost"], ["", "85.00"])
        self.assertEqual(e.changes["Notes"], ["", "Replaced float switch"]); self.assertEqual(e.changes["Completed on"], ["", "2026-11-14"])
        self.assertEqual((e.target_type, e.actor), ("taskoccurrence", self.alice))
        with use_account(self.acme):
            self.assertEqual(TaskOccurrence.objects.filter(task=self.o.task).count(), 2)          # the next one exists...
        self.assertEqual(self.actions(action="created"), [])                                        # ...without a "created" entry

    def test_skip_reopen_and_reschedule(self):
        with use_account(self.acme), acting_as(self.alice):
            skip_occurrence(TaskOccurrence.objects.get(pk=self.o.pk), today=D(2026, 11, 15), user=self.alice)
            o = TaskOccurrence.objects.get(pk=self.o.pk); o.status = "open"; o.completed_by = None; o.save()
            o.due_date = D(2026, 11, 20); o.save()
        self.assertEqual(self.actions(), ["skipped", "reopened", "updated"])
        self.assertEqual(self.log()[-1].changes, {"Due date": ["2026-11-15", "2026-11-20"]})


class FileAndLinkTests(AuditWorld):
    def test_file_events_appear_in_the_parents_history(self):
        with acting_as(self.alice):
            att = self.store(self.maple, "invoice.png", png())
            with use_account(self.acme):
                att = type(att).objects.get(pk=att.pk); att.caption = "Final"; att.save()
            from attachments.services import delete_attachment
            with use_account(self.acme), self.captureOnCommitCallbacks(execute=True):
                delete_attachment(type(att).objects.get(pk=att.pk))
        entries = self.log(target_type="property", target_id=self.maple.pk)
        self.assertEqual([e.action for e in entries], ["uploaded", "updated", "deleted"])
        self.assertEqual(entries[0].summary, "Uploaded file “invoice.png” to property “Maple Duplex”")
        self.assertEqual(entries[1].changes, {"Caption": ["", "Final"]})
        self.assertEqual(entries[2].summary, "Deleted file “invoice.png” from property “Maple Duplex”")

    def test_file_on_a_job_is_filed_under_the_job(self):
        o = self.occ(self.acme, self.maple, "Pump test", D(2026, 11, 15))
        ActivityLog.all_tenants.all().delete()
        self.store(o, "receipt.png", png())
        e, = self.log()
        self.assertEqual((e.target_type, e.target_id, e.target_label), ("taskoccurrence", o.pk, "Pump test"))

    def test_linking_and_unlinking_contacts_to_properties(self):
        self.login(self.alice)
        base = {"name": "Dana Pipes", "role": "vendor"}
        self.client.post(f"/contacts/{self.plumber.pk}/", {**base, "properties": [self.maple.pk, self.oak.pk]})
        self.client.post(f"/contacts/{self.plumber.pk}/", {**base, "properties": [self.oak.pk]})
        linked = self.log(action="linked"); unlinked = self.log(action="unlinked")
        self.assertEqual(sorted(e.summary for e in linked), ["Linked contact “Dana Pipes” to property “Maple Duplex”", "Linked contact “Dana Pipes” to property “Oak Cottage”"])
        self.assertEqual([e.summary for e in unlinked], ["Unlinked contact “Dana Pipes” from property “Maple Duplex”"])
        self.assertTrue(all(e.target_type == "contact" and e.target_id == self.plumber.pk for e in linked + unlinked))


class ThroughTheWebTests(AuditWorld):
    def test_the_signed_in_user_is_recorded_for_changes_made_in_pages(self):
        self.login(self.alice)
        self.client.post("/properties/new/", {"name": "Birch Lodge", "property_type": "single_family", "city": "Ogdenville"})
        pk = Property.all_tenants.get(name="Birch Lodge").pk
        self.client.post(f"/properties/{pk}/", {"name": "Birch Lodge", "property_type": "single_family", "city": "Springfield"})
        self.client.post(f"/properties/{pk}/archive/")
        self.login(self.carol)
        self.client.post(f"/properties/{self.oak.pk}/", {"name": "Oak Cottage", "property_type": "single_family", "city": "Elsewhere"})
        by = [(e.action, e.actor_email) for e in self.log()]
        self.assertEqual(by, [("created", "alice@acme.test"), ("updated", "alice@acme.test"), ("archived", "alice@acme.test"), ("updated", "carol@both.test")])

    def test_a_request_never_leaks_its_actor_into_the_next_one(self):
        from audit.context import get_actor
        self.login(self.alice); self.client.get("/")
        self.assertIsNone(get_actor())


class IntegrityTests(AuditWorld):
    def test_entries_belong_to_the_account_of_the_record(self):
        with use_account(self.beta):
            Property.objects.create(name="Beta Place")
        self.assertEqual(len(self.log(self.beta)), 1); self.assertEqual(self.log(self.acme), [])
        self.assertEqual(ActivityLog.all_tenants.get().account_id, self.beta.pk)

    def test_a_rolled_back_change_leaves_no_trace_in_the_log(self):
        try:
            with transaction.atomic(), use_account(self.acme):
                Property.objects.create(name="Phantom"); raise RuntimeError("later step failed")
        except RuntimeError:
            pass
        self.assertEqual((self.log(), Property.all_tenants.filter(name="Phantom").count()), ([], 0))

    def test_failing_to_write_the_log_fails_the_save(self):
        with mock.patch.object(ActivityLog, "save", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError), transaction.atomic(), use_account(self.acme):
                Property.objects.create(name="Unlogged")
        self.assertEqual(Property.all_tenants.filter(name="Unlogged").count(), 0)

    def test_cosmetic_rendering_problems_never_block_a_save(self):
        field = InventoryItem._meta.get_field("property")
        self.assertEqual(display(field, 999999), "#999999")                         # row no longer exists
        with mock.patch("audit.registry._label_for_fk", side_effect=RuntimeError("boom")):
            self.assertEqual(display(field, self.maple.pk), str(self.maple.pk))
        with use_account(self.acme), mock.patch("audit.registry._label_for_fk", side_effect=RuntimeError("boom")):
            it = InventoryItem.objects.get(pk=self.heater.pk); it.property, it.unit = self.oak, None; it.save()
        self.assertEqual(len(self.log()), 1)

    def test_logging_costs_one_select_and_one_insert_per_save(self):
        with use_account(self.acme):
            p = Property.objects.get(pk=self.maple.pk); p.name = "Maple Duplex II"
            with self.assertNumQueries(3):                                          # UPDATE + snapshot SELECT + log INSERT
                p.save()


class AppendOnlyTests(AuditWorld):
    def entry(self):
        with use_account(self.acme):
            Property.objects.create(name="X")
        return self.log()[0]

    def test_entries_cannot_be_edited_or_deleted(self):
        e = self.entry()
        e.summary = "rewritten"
        with self.assertRaises(ImmutableLog):
            e.save()
        with self.assertRaises(ImmutableLog):
            e.delete()
        self.assertEqual(ActivityLog.all_tenants.get(pk=e.pk).summary, "Created property “X”")

    def test_the_admin_is_read_only(self):
        ma = ActivityLogAdmin(ActivityLog, admin.site)
        request = RequestFactory().get("/admin/"); request.user = self.alice
        e = self.entry()
        self.assertEqual((ma.has_add_permission(request), ma.has_change_permission(request, e), ma.has_delete_permission(request, e)),
                         (False, False, False))
