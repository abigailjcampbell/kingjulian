from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from contacts.models import Contact, ContactProperty
from core.tenancy import (TenantContextMissing, TenantViolation, get_current_account_id,
                          use_account)
from inventory.models import InventoryItem
from properties.models import Property, Unit

from .base import World


class ManagerScopingTests(World):
    def test_queries_fail_closed_without_context(self):
        for call in (lambda: list(Property.objects.all()), Property.objects.count,
                     Property.objects.exists, lambda: Property.objects.active().first()):
            with self.assertRaises(TenantContextMissing):
                call()

    def test_manager_only_sees_active_account(self):
        with use_account(self.acme):
            names = set(Property.objects.values_list("name", flat=True))
            self.assertEqual(names, {"Maple Duplex", "Oak Cottage"})
            self.assertFalse(Property.objects.filter(pk=self.lake.pk).exists())
            self.assertFalse(InventoryItem.objects.filter(pk=self.beta_item.pk).exists())
        with use_account(self.beta):
            self.assertEqual(set(Property.objects.values_list("name", flat=True)), {"Lakeview Tower"})

    def test_all_tenants_is_unscoped(self):
        self.assertEqual(Property.all_tenants.count(), 3)

    def test_context_is_reset_after_use_account(self):
        with use_account(self.acme):
            self.assertEqual(get_current_account_id(), self.acme.pk)
        self.assertIsNone(get_current_account_id())

    def test_nested_context_restores_outer(self):
        with use_account(self.acme):
            with use_account(self.beta):
                self.assertEqual(get_current_account_id(), self.beta.pk)
            self.assertEqual(get_current_account_id(), self.acme.pk)

    def test_class_level_queryset_rescopes_per_execution(self):
        qs = Property.objects.active()          # built with NO context: must not raise
        with use_account(self.acme):
            acme_names = {p.name for p in qs.all()}
        with use_account(self.beta):
            beta_names = {p.name for p in qs.all()}
        self.assertEqual(acme_names, {"Maple Duplex", "Oak Cottage"})
        self.assertEqual(beta_names, {"Lakeview Tower"})


class WriteGuardTests(World):
    def test_save_takes_account_from_context(self):
        with use_account(self.acme):
            p = Property(name="New One")
            p.save()
        self.assertEqual(p.account_id, self.acme.pk)

    def test_save_without_account_or_context_raises(self):
        with self.assertRaises(TenantContextMissing):
            Property(name="Orphan").save()

    def test_save_into_other_account_while_active_raises(self):
        with use_account(self.acme):
            with self.assertRaises(TenantViolation):
                Property(account=self.beta, name="Sneaky").save()

    def test_fk_to_other_tenants_row_rejected(self):
        with use_account(self.acme):
            with self.assertRaises(TenantViolation):
                InventoryItem(property=self.lake, name="Stolen link").save()
            with self.assertRaises(TenantViolation):
                Unit(property=self.lake, name="Bad unit").save()

    def test_fk_check_also_covers_unscoped_writes(self):
        # Even via all_tenants with an explicit account, mixing tenants is refused.
        with self.assertRaises(TenantViolation):
            InventoryItem(account=self.acme, property=self.lake, name="x").save()

    def test_through_table_rejects_cross_tenant_pair(self):
        with use_account(self.acme):
            with self.assertRaises(TenantViolation):
                ContactProperty(contact=self.plumber, property=self.lake).save()

    def test_unit_must_belong_to_property(self):
        with use_account(self.acme):
            item = InventoryItem(property=self.oak, unit=self.maple_a, name="Mismatch")
            with self.assertRaises(ValidationError):
                item.save()
            with self.assertRaises(ValidationError):
                item.full_clean()

    def test_unit_name_unique_per_property_among_active(self):
        with use_account(self.acme):
            with self.assertRaises(IntegrityError), transaction.atomic():
                Unit.objects.create(property=self.maple, name="Unit A")
            self.maple_a.archive()
            Unit.objects.create(property=self.maple, name="Unit A")   # name freed by archiving


class SoftDeleteTests(World):
    def test_archive_hides_from_active_but_keeps_row(self):
        with use_account(self.acme):
            self.heater.archive()
            self.assertNotIn(self.heater.pk, InventoryItem.objects.active().values_list("pk", flat=True))
            self.assertIn(self.heater.pk, InventoryItem.objects.archived().values_list("pk", flat=True))
            self.assertTrue(InventoryItem.objects.get(pk=self.heater.pk).is_archived)
            self.heater.restore()
            self.assertIn(self.heater.pk, InventoryItem.objects.active().values_list("pk", flat=True))
