"""Verify that every hand-applied patch landed.  Usage:  python manage.py check_setup

Prints PASS/FAIL per item with the fix to apply, and exits non-zero if anything is missing."""
import dataclasses
import importlib
import inspect
import sys
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.template.loader import get_template
from django.urls import NoReverseMatch, reverse


def template_text(name):
    return Path(get_template(name).origin.name).read_text()


def source_has(obj, needle):
    return needle in inspect.getsource(obj)


class Check:
    def __init__(self, name, fn, fix, requires=None):
        self.name, self.fn, self.fix, self.requires = name, fn, fix, requires      # requires: a check that must pass first


def mod(dotted):
    """Import lazily so a broken module is reported as a failed check instead of crashing the checker."""
    return __import__(dotted, fromlist=["_"])


def build_checks():
    checks = []
    add = lambda name, fn, fix, requires=None: checks.append(Check(name, fn, fix, requires))
    L = lambda: mod("core.crud").TenantListView
    F = lambda: mod("core.crud").TenantFormMixin

    # ---- core/crud.py -------------------------------------------------------------------------
    add("core/crud.py imports cleanly", lambda: bool(mod("core.crud")), "core/crud.py has a syntax or import error; compare it with the patches.")
    add("TenantListView.has_create", lambda: hasattr(L(), "has_create"), "Step 3, section 4: add `has_create = True` under `paginate_by = 25` in TenantListView.")
    add("TenantListView.viewers_can_open", lambda: hasattr(L(), "viewers_can_open"), "Step 3, section 4: add `viewers_can_open = False` in TenantListView.")
    add("TenantListView.export_key", lambda: hasattr(L(), "export_key"), "Step 5, section 3: add `export_key = None` in TenantListView.")
    add("TenantListView.get / export_csv", lambda: hasattr(L(), "export_csv") and "export" in inspect.getsource(L().get), "Step 5, section 3: add the `get` and `export_csv` methods to TenantListView.")
    add("TenantListView context has link_rows and export_url", lambda: source_has(L().get_context_data, "link_rows") and source_has(L().get_context_data, "export_url"), "Step 3 and step 5, section 3: replace TenantListView.get_context_data with the version in PATCHES-step5.md.")
    add("audit quiet mode exists and is honoured", lambda: hasattr(mod("audit.context"), "quiet") and source_has(mod("audit.signals").record, "is_quiet"), "Permanent-delete patch, sections 4 and 5: add quiet()/is_quiet() to audit/context.py and the early return to audit.signals.record.")
    add("human_size formats sizes to one decimal", lambda: mod("attachments.validators").human_size(1484) == "1.4 KB", "Permanent-delete patch, section 6: replace human_size in attachments/validators.py.")
    add("permanent delete is wired up", lambda: bool(mod("archive.delete_views")) and bool(mod("archive.deletion")) and bool(reverse("archive_delete", args=["contact", 1])), "Permanent-delete patch, section 1: add the archive_delete path (and the delete_views import) to archive/urls.py; unzip the new files.")
    add("TenantArchiveView offers an Undo link", lambda: hasattr(mod("core.crud").TenantArchiveView, "archived_message"), "Archive patch, section 3: replace TenantArchiveView.post and add archived_message.")
    add("attachments panel hides uploads on archived records", lambda: source_has(mod("attachments.templatetags.attachment_tags").attachments_panel, "archived_at"), "Archive patch, section 5: replace attachments_panel in attachments/templatetags/attachment_tags.py.")
    add("list rows open read-only pages", lambda: hasattr(L(), "row_url_name"), "Read-only pages patch, section 3: add row_url_name and use it in TenantListView.get_context_data.")
    add("editing returns to the record", lambda: hasattr(F(), "cancel_url") and source_has(mod("core.crud").TenantUpdateView.get_success_url, "cancel_url"), "Read-only pages patch, section 3: add TenantFormMixin.cancel_url and TenantUpdateView.get_success_url.")
    add("units live under their property", lambda: hasattr(mod("properties.views"), "PropertyUnits") and not hasattr(mod("properties.views"), "UnitList"), "Units patch, section 5: replace UnitList with PropertyUnits (and the unit form/archive views) in properties/views.py.")
    add("TenantArchiveView.after_archive_url", lambda: hasattr(mod("core.crud").TenantArchiveView, "after_archive_url"), "Units patch, section 2: add after_archive_url to TenantArchiveView and use it in post.")
    add("TenantFormMixin.get_list_url", lambda: hasattr(F(), "get_list_url"), "Units patch, section 2: add get_list_url to TenantFormMixin and use it for list_url, get_success_url and cancel_url.")
    add("TenantListView.get_create_url and back link", lambda: hasattr(L(), "get_create_url") and hasattr(L(), "back_url"), "Units patch, section 2: add back_url/back_label, get_create_url and the new context keys to TenantListView.")
    add("TenantFormMixin.archivable", lambda: hasattr(F(), "archivable"), "Step 3: add `archivable = True` under `requires_write = True` in TenantFormMixin.")
    add("TenantFormMixin uses archivable", lambda: source_has(F().get_context_data, "self.archivable"), "Step 3: in TenantFormMixin.get_context_data use `if self.archivable and getattr(self, \"object\", None) and self.object.pk:`.")
    add("Filter has default and apply", lambda: {"default", "apply"} <= {f.name for f in dataclasses.fields(mod("core.crud").Filter)}, "Step 3 and step 4, section 4: add the `default` and `apply` fields to the Filter dataclass (an error such as \"unexpected keyword argument 'apply'\" means this).")
    add("TenantListView.get_queryset honours Filter.default/apply", lambda: source_has(L().get_queryset, "flt.default") and source_has(L().get_queryset, "flt.apply"), "Step 3 and 4: update TenantListView.get_queryset (see PATCHES-step4.md, section 4).")

    # ---- list views opt in to export --------------------------------------------------------------
    expected = {"properties.views.PropertyList": "properties", "properties.views.PropertyUnits": "units", "contacts.views.ContactList": "contacts",
                "inventory.views.InventoryList": "inventory", "maintenance.views.TaskList": "tasks", "maintenance.views.OccurrenceList": "history",
                "planner.views.EventList": "events"}
    for dotted, key in expected.items():
        module, cls = dotted.rsplit(".", 1)
        add(f"{cls}.export_key", lambda m=module, c=cls, k=key: getattr(mod(m), c).export_key == k,
            f"Step 5, section 4: add `export_key = \"{key}\"` to class {cls} in {module.replace('.', '/')}.py (if the module itself fails to import, see the error shown).")

    # ---- settings --------------------------------------------------------------------------------
    for app in ("accounts", "core", "properties", "contacts", "inventory", "maintenance", "planner", "attachments", "audit", "exports", "archive", "details"):
        add(f"INSTALLED_APPS has {app}", lambda a=app: apps.is_installed(a), f"Add \"{app}\" to INSTALLED_APPS (audit may be \"audit.apps.AuditConfig\").")
    mw = list(settings.MIDDLEWARE)
    idx = lambda name: mw.index(name) if name in mw else -1
    add("middleware: ActiveAccountMiddleware", lambda: idx("core.middleware.ActiveAccountMiddleware") >= 0, "Add core.middleware.ActiveAccountMiddleware after AuthenticationMiddleware.")
    add("middleware: UploadSizeMiddleware before CSRF", lambda: 0 <= idx("attachments.middleware.UploadSizeMiddleware") < idx("django.middleware.csrf.CsrfViewMiddleware"), "Step 5, section 1: add attachments.middleware.UploadSizeMiddleware right after SecurityMiddleware (before CsrfViewMiddleware).")
    add("middleware: ActorMiddleware after ActiveAccount", lambda: idx("audit.middleware.ActorMiddleware") > idx("core.middleware.ActiveAccountMiddleware") >= 0, "Step 5, section 1: add audit.middleware.ActorMiddleware after ActiveAccountMiddleware.")
    for name in ("MEDIA_ROOT", "ATTACHMENT_MAX_BYTES", "ATTACHMENT_ACCOUNT_QUOTA_BYTES", "ATTACHMENT_MAX_PER_TARGET", "FILE_UPLOAD_PERMISSIONS", "SITE_URL", "EMAIL_BACKEND"):
        add(f"setting {name}", lambda n=name: hasattr(settings, n), f"Add {name} to config/settings.py (Step 5 section 1 / Step 3 section 1).")
    add("AUTH_USER_MODEL", lambda: settings.AUTH_USER_MODEL == "accounts.User", "Set AUTH_USER_MODEL = \"accounts.User\".")

    # ---- timezone ----------------------------------------------------------------------------------
    add("Account.timezone field", lambda: "timezone" in {f.name for f in apps.get_model("accounts", "Account")._meta.fields}, "Timezone patch, section 2: add the timezone field to Account (and run migrate).")
    add("middleware activates the account timezone", lambda: source_has(mod("core.middleware").ActiveAccountMiddleware.__call__, "timezone.activate") and source_has(mod("core.middleware").ActiveAccountMiddleware.__call__, "timezone.deactivate"), "Timezone patch, section 6: replace ActiveAccountMiddleware.__call__.")
    add("reminder job uses each account's own date", lambda: source_has(mod("maintenance.management.commands.send_maintenance_reminders").Command.handle, "account.today()"), "Timezone patch, section 7: replace both methods of Command in send_maintenance_reminders.py.")
    add("ics converts times to UTC", lambda: hasattr(mod("planner.ical"), "_utc"), "Timezone patch, section 8: add _utc and replace _event_lines / build_ics in planner/ical.py.")
    add("signup has timezone field", lambda: "timezone" in mod("accounts.forms").SignupForm.base_fields, "Timezone patch, section 3: replace SignupForm.")

    # ---- URLs -----------------------------------------------------------------------------------------
    add("URLconf imports cleanly", lambda: bool(importlib.import_module(settings.ROOT_URLCONF)),
        "Importing the URL configuration fails. Read the error shown: it names the module that cannot load, which is usually a missing patch in that module's dependencies (for example the Filter dataclass).")
    for name in ("home", "property_list", "property_units", "contact_list", "inventoryitem_list", "task_list", "occurrence_list", "event_list", "calendar",
                 "agenda", "calendar_ics", "attachment_list", "activity_list", "export_hub", "archive_list", "property_detail", "unit_detail", "inventoryitem_detail", "contact_detail", "task_detail", "event_detail", "account_settings", "switch_account"):
        def resolves(n=name):
            for args in ([], [1]):                      # most routes take no argument; detail routes take an id
                try:
                    reverse(n, args=args)
                    return True
                except NoReverseMatch:
                    continue
            return False
        add(f"url name '{name}'", resolves, "A urls.py include is missing: see config/urls.py in the step patches (planner/, files/, activity/, exports/, maintenance/) and accounts/urls.py.",
            requires="URLconf imports cleanly")

    # ---- templates ---------------------------------------------------------------------------------------
    t = lambda tpl, *needles: (lambda: all(n in template_text(tpl) for n in needles))
    add("base.html nav links", t("base.html", "occurrence_list", "task_list", "'calendar'", "attachment_list", "activity_list", "export_hub", "archive_list", "account_settings"), "Add the nav links from the step 3, 4, 5 and timezone patches (Maintenance, Tasks, Calendar, Files, Activity, Export, Settings) to templates/base.html.")
    add("crud/list.html export button", t("crud/list.html", "export_url"), "Step 5, section 5: add the Export CSV link to templates/crud/list.html.")
    add("base.html has no Units tab", lambda: "unit_list" not in template_text("base.html"), "Units patch, section 7: delete the `<a href=\"{% url 'unit_list' %}\">Units</a>` line from templates/base.html.")
    add("crud/list.html back link", t("crud/list.html", "back_url"), "Units patch, section 7: add the back_url line above <h1> in templates/crud/list.html.")
    add("record page section Add button", t("details/detail.html", "add_url"), "Units patch, section 4: update templates/details/detail.html (h2 with add_url, empty_text).")
    add("crud/_results.html link_rows", t("crud/_results.html", "link_rows"), "Step 3, section 6: use `forloop.first and link_rows` in templates/crud/_results.html.")
    add("crud/form.html Cancel returns to the record", t("crud/form.html", "cancel_url"), "Read-only pages patch, section 4: use `cancel_url|default:list_url` for the Cancel link in templates/crud/form.html (and remove the Files/History panels from it).")
    add("record page has the Files and History panels", t("details/detail.html", "attachments_panel", "history_panel"), "Unzip the details app (details/templates/details/detail.html).")
    add("dashboard links to read-only item pages", t("planner/dashboard.html", "inventoryitem_detail", "event_detail"), "Read-only pages patch, section 6: link events and inventory items on the dashboard to their *_detail pages.")
    add("occurrence_form.html panels", t("maintenance/occurrence_form.html", "attachments_panel", "history_panel"), "Step 5, section 5: add the {% load %} line and the two panel tags to maintenance/templates/maintenance/occurrence_form.html.")
    add("signup.html timezone script", t("accounts/signup.html", "Intl.DateTimeFormat"), "Timezone patch, section 10: add the script to templates/accounts/signup.html.")

    # ---- database ---------------------------------------------------------------------------------------------
    def migrations_applied():
        executor = MigrationExecutor(connection)
        return not executor.migration_plan(executor.loader.graph.leaf_nodes())
    add("all migrations applied", migrations_applied, "Run: python manage.py migrate")
    return checks


class Command(BaseCommand):
    help = "Check that every hand-applied patch is in place."
    # Django's own system checks import the URLconf, which fails when a patch is missing. This command exists
    # to diagnose exactly that, so it must still run.
    requires_system_checks = []

    def handle(self, *args, **options):
        failures, failed, checks = 0, set(), build_checks()
        for c in checks:
            if c.requires in failed:
                self.stdout.write(f"  SKIP  {c.name}  (blocked by: {c.requires})")
                continue
            try:
                ok, note = bool(c.fn()), ""
            except Exception as exc:                       # a missing module/attribute is a failed check, not a crash
                ok, note = False, f"  (error: {type(exc).__name__}: {exc})"
            if ok:
                self.stdout.write(f"  PASS  {c.name}")
            else:
                failures += 1
                failed.add(c.name)
                self.stdout.write(self.style.ERROR(f"  FAIL  {c.name}\n        fix: {c.fix}{note}"))
        if failures:
            self.stdout.write(self.style.ERROR(f"\n{failures} check(s) failed. Fix those, then run this again: skipped checks run once their blocker passes."))
            sys.exit(1)
        self.stdout.write(self.style.SUCCESS(f"\nAll {len(checks)} checks passed."))
