from core.crud import (Filter, TenantArchiveView, TenantCreateView, TenantListView,
                       TenantUpdateView)

from .forms import ContactForm
from .models import Contact


class ContactList(TenantListView):
    model = Contact
    url_basename = "contact"
    title = "Contacts"
    columns = [("Name", "name"), ("Role", "get_role_display"), ("Company", "company"),
               ("Phone", "phone"), ("Email", "email")]
    search_fields = ("name", "company", "email", "phone", "notes")
    filters = [Filter("role", "role", "Role", lambda: list(Contact.Role.choices))]


class ContactCreate(TenantCreateView):
    model, form_class, url_basename, title = Contact, ContactForm, "contact", "New contact"


class ContactUpdate(TenantUpdateView):
    model, form_class, url_basename, title = Contact, ContactForm, "contact", "Edit contact"


class ContactArchive(TenantArchiveView):
    model, url_basename = Contact, "contact"
