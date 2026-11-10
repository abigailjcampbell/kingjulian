from django.contrib import admin

from core.admin import TenantAdmin

from .models import Contact, ContactProperty


@admin.register(Contact)
class ContactAdmin(TenantAdmin):
    list_display = ("name", "role", "company", "phone", "account", "archived_at")
    search_fields = ("name", "company", "email", "phone")


@admin.register(ContactProperty)
class ContactPropertyAdmin(TenantAdmin):
    list_display = ("contact", "property", "account")
