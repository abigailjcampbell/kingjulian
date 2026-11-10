from django.db import models
from django.utils import timezone

from .tenancy import (
    TenantViolation,
    get_current_account_id,
    require_current_account_id,
)


class TenantQuerySet(models.QuerySet):
    def active(self):
        return self.filter(archived_at__isnull=True)

    def archived(self):
        return self.filter(archived_at__isnull=False)


class CurrentAccountId(models.Expression):
    """Resolved when the SQL is compiled, not when the queryset is built.

    Building the queryset needs no tenant context (Django builds ModelForm FK querysets at
    import time), but *executing* it without one raises TenantContextMissing. It also means a
    queryset stored on a class re-scopes itself to whichever account is active at execution.
    """

    def __init__(self):
        super().__init__(output_field=models.BigIntegerField())

    def as_sql(self, compiler, connection):
        return "%s", [require_current_account_id()]


class TenantManager(models.Manager.from_queryset(TenantQuerySet)):
    """Default manager: always filtered to the active account; fails closed without one."""

    def get_queryset(self):
        return super().get_queryset().filter(account_id=CurrentAccountId())


class TenantModel(models.Model):
    """Base for every tenant-owned table.

    `objects`      -> scoped to the active account (raises if none is active)
    `all_tenants`  -> unscoped; for admin, management commands, and tests only

    Always build ModelForms with an explicit `fields` list so `account` is never user-editable.
    """

    account = models.ForeignKey("accounts.Account", on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    objects = TenantManager()
    all_tenants = models.Manager.from_queryset(TenantQuerySet)()

    class Meta:
        abstract = True

    # -- tenancy guards -------------------------------------------------
    def save(self, *args, **kwargs):
        active = get_current_account_id()
        if self.account_id is None:
            self.account_id = require_current_account_id()
        elif active is not None and self.account_id != active:
            raise TenantViolation(
                f"{type(self).__name__} belongs to account {self.account_id} "
                f"but the active account is {active}."
            )
        self._validate_tenant_links()
        super().save(*args, **kwargs)

    def _validate_tenant_links(self):
        """Every FK to another tenant model must point inside the same account."""
        for field in self._meta.concrete_fields:
            if not (field.many_to_one and issubclass(field.related_model, TenantModel)):
                continue
            if getattr(self, field.attname) is None:
                continue
            related = getattr(self, field.name)  # fetched via the unscoped base manager
            if related.account_id != self.account_id:
                raise TenantViolation(
                    f"{type(self).__name__}.{field.name} points at another account's row."
                )

    # -- soft delete ----------------------------------------------------
    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    def archive(self):
        self.archived_at = timezone.now()
        self.save(update_fields=["archived_at", "updated_at"])

    def restore(self):
        self.archived_at = None
        self.save(update_fields=["archived_at", "updated_at"])
