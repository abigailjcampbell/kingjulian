from django.contrib import admin

from .models import TenantModel


class TenantAdmin(admin.ModelAdmin):
    """Admin for tenant models. Staff see all tenants (via all_tenants) and pick the account."""

    list_filter = ("account",)

    def get_queryset(self, request):
        qs = self.model.all_tenants.get_queryset()
        ordering = self.get_ordering(request)
        return qs.order_by(*ordering) if ordering else qs

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        related = db_field.remote_field.model
        if issubclass(related, TenantModel):
            kwargs["queryset"] = related.all_tenants.all()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        related = db_field.remote_field.model
        if issubclass(related, TenantModel):
            kwargs["queryset"] = related.all_tenants.all()
        return super().formfield_for_manytomany(db_field, request, **kwargs)
