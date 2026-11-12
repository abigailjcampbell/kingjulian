import re
from datetime import date as D, datetime, time as T, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest import mock

from accounts.models import Account, Membership, User
from audit.context import acting_as
from audit.models import ActivityLog
from contacts.models import Contact, ContactProperty
from core.tenancy import use_account
from core.tests.base import PW
from exports.datasets import DATASETS
from maintenance.services import complete_occurrence
from properties.models import Property

from attachments.tests.base import png
from .base import ExportWorld, parse, zip_members

EXPECTED_HEADERS = {
    "properties": ["Name", "Type", "Address line 1", "Address line 2", "City", "State", "Postal code", "Notes", "Archived"],
    "units": ["Property", "Unit", "Notes", "Archived"],
    "inventory": ["Item", "Property", "Unit", "Category", "Quantity", "Serial number", "Purchase date", "Cost", "Condition",
                  "Warranty expires", "Notes", "Archived"],
    "contacts": ["Name", "Role", "Company", "Phone", "Email", "Properties", "Notes", "Archived"],
    "tasks": ["Task", "Property", "Unit", "Item", "Assigned to", "Start date", "Repeats", "Reminder days before", "Next due",
              "Description", "Archived"],
    "history": ["Task", "Property", "Item", "Due date", "Status", "Completed on", "Completed by", "Cost", "Notes"],
    "events": ["Event", "Type", "Date", "Last day", "Start time", "End time", "Property", "With", "Notes", "Archived"],
    "attachments": ["File", "Attached to", "Record", "Size (bytes)", "Type", "Caption", "Uploaded by", "Uploaded", "SHA-256"],
    "activity": ["When", "Who", "Action", "Object type", "Object", "Summary", "Changes"],
}


class RichData(ExportWorld):
    def setUp(self):
        self.login(self.alice)
        with use_account(self.acme):
            ContactProperty.objects.create(contact=self.plumber, property=self.maple)
            ContactProperty.objects.create(contact=self.plumber, property=self.oak)
            self.heater.cost, self.heater.warranty_expires_on, self.heater.purchase_date = Decimal("1450.00"), D(2027, 3, 1), D(2024, 3, 1)
            self.heater.save()
        self.task_occ = self.occ(self.acme, self.maple, "Flush heater", D(2026, 12, 1), item=self.heater)
        with use_account(self.acme):
            t = self.task_occ.task
            t.recurrence_rule, t.assigned_contact = "FREQ=YEARLY", self.plumber; t.save()
        self.done = self.occ(self.acme, self.oak, "Gutters", D(2026, 10, 1))
        with use_account(self.acme), acting_as(self.alice):            # as the middleware does for a real request
            complete_occurrence(type(self.done).objects.get(pk=self.done.pk), completed_on=D(2026, 10, 2), user=self.alice, cost=Decimal("85.50"), notes="Cleared, 2 bags")
        self.event(self.acme, "City inspection", D(2026, 11, 20), start_time=T(10, 0), end_time=T(12, 0), property=self.maple, contact=self.plumber)
        self.event(self.acme, "Roof work", D(2026, 11, 30), ends_on=D(2026, 12, 2))
        self.store(self.maple, "invoice.png", png(), caption="Boiler service, 2026")


class DatasetContentTests(RichData):
    def test_every_dataset_has_exactly_the_documented_columns(self):
        self.assertEqual(set(DATASETS), set(EXPECTED_HEADERS))
        for key, headers in EXPECTED_HEADERS.items():
            head, _ = self.table(f"/exports/{key}.csv")
            self.assertEqual(head, headers, key)

    def rows(self, key):
        head, rows = self.table(f"/exports/{key}.csv")
        return [dict(zip(head, r)) for r in rows]

    def test_properties_units_inventory(self):
        props = self.rows("properties")
        self.assertEqual([p["Name"] for p in props], ["Maple Duplex", "Oak Cottage"])
        self.assertEqual(props[0]["Type"], "Multi-family"); self.assertEqual(props[0]["City"], "Springfield")
        self.assertEqual([(u["Property"], u["Unit"]) for u in self.rows("units")], [("Maple Duplex", "Unit A"), ("Maple Duplex", "Unit B")])
        heater = next(i for i in self.rows("inventory") if i["Item"] == "Water Heater 50gal")
        self.assertEqual((heater["Property"], heater["Unit"], heater["Category"], heater["Serial number"], heater["Cost"],
                          heater["Purchase date"], heater["Warranty expires"], heater["Condition"]),
                         ("Maple Duplex", "Unit A", "HVAC", "WH-884213", "1450.00", "2024-03-01", "2027-03-01", "Good"))

    def test_contacts_list_their_properties(self):
        c, = self.rows("contacts")
        self.assertEqual((c["Name"], c["Role"], c["Company"], c["Properties"]), ("Dana Pipes", "Vendor", "Pipes & Co", "Maple Duplex; Oak Cottage"))

    def test_tasks_show_schedule_reminders_and_next_due(self):
        t = next(r for r in self.rows("tasks") if r["Task"] == "Flush heater")
        self.assertEqual((t["Repeats"], t["Reminder days before"], t["Next due"], t["Assigned to"], t["Item"], t["Unit"]),
                         ("Yearly", "7, 1", "2026-12-01", "Dana Pipes", "Water Heater 50gal", ""))

    def test_history_has_who_what_and_how_much(self):
        done = next(r for r in self.rows("history") if r["Task"] == "Gutters" and r["Status"] == "Done")
        self.assertEqual((done["Completed on"], done["Completed by"], done["Cost"], done["Notes"], done["Property"]),
                         ("2026-10-02", "alice@acme.test", "85.50", "Cleared, 2 bags", "Oak Cottage"))

    def test_events_files_and_activity(self):
        ev = {r["Event"]: r for r in self.rows("events")}
        self.assertEqual((ev["City inspection"]["Start time"], ev["City inspection"]["End time"], ev["City inspection"]["With"]), ("10:00", "12:00", "Dana Pipes"))
        self.assertEqual((ev["Roof work"]["Date"], ev["Roof work"]["Last day"], ev["Roof work"]["Start time"]), ("2026-11-30", "2026-12-02", ""))
        f, = self.rows("attachments")
        self.assertEqual((f["File"], f["Attached to"], f["Record"], f["Caption"], f["Uploaded by"], f["Type"]),
                         ("invoice.png", "Property", "Maple Duplex", "Boiler service, 2026", "alice@acme.test", "image/png"))
        self.assertTrue(f["Size (bytes)"].isdigit() and len(f["SHA-256"]) == 64)
        acts = self.rows("activity")
        self.assertTrue(any(a["Action"] == "Completed" and a["Who"] == "alice@acme.test" for a in acts))
        self.assertRegex(acts[0]["When"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")

    def test_hub_exports_include_archived_rows_with_the_date_filled_in(self):
        with use_account(self.acme):
            self.oak.archive()
        props = {p["Name"]: p["Archived"] for p in self.rows("properties")}
        self.assertEqual((props["Maple Duplex"], bool(props["Oak Cottage"])), ("", True))

    def test_other_tenants_rows_never_appear_in_any_dataset(self):
        with use_account(self.beta):
            ActivityLog.objects.count()
        self.occ(self.beta, self.lake, "BETA-ONLY job", D(2026, 12, 1), status="done", cost=Decimal("9999"))
        self.event(self.beta, "BETA-ONLY event", D(2026, 12, 1)); self.item(self.beta, self.lake, "BETA-ONLY item")
        self.store(self.lake, "BETA-ONLY.png", png(), account=self.beta, user=self.bella)
        for key in DATASETS:
            r = self.client.get(f"/exports/{key}.csv")
            self.assertNotIn("BETA", r.content.decode(), key); self.assertNotIn("Lakeview", r.content.decode(), key)


class ListExportTests(RichData):
    def test_export_follows_the_filters_and_search_on_screen_and_ignores_pagination(self):
        for i in range(60):
            self.item(self.acme, self.oak, f"Smoke Detector {i:02d}", category="safety")
        head, rows = self.table("/inventory/?export=csv")
        self.assertEqual(len(rows), 62)                                                  # all rows, not one page of 25
        head, rows = self.table("/inventory/?category=safety&export=csv")
        self.assertEqual(len(rows), 60); self.assertTrue(all(r[3] == "Safety (smoke detector, extinguisher)" for r in rows))
        head, rows = self.table("/inventory/?q=heater&export=csv")
        self.assertEqual([r[0] for r in rows], ["Water Heater 50gal"])

    def test_list_exports_leave_out_archived_rows_but_the_hub_keeps_them(self):
        with use_account(self.acme):
            self.oak.archive()
        self.assertEqual([r[0] for r in self.table("/properties/?export=csv")[1]], ["Maple Duplex"])
        self.assertEqual(len(self.table("/exports/properties.csv")[1]), 2)

    def test_download_headers(self):
        r = self.client.get("/inventory/?export=csv")
        self.assertEqual(r["Content-Type"], "text/csv; charset=utf-8")
        self.assertRegex(r["Content-Disposition"], r'^attachment; filename="inventory-acme-rentals-\d{4}-\d\d-\d\d\.csv"$')
        self.assertEqual(r["X-Content-Type-Options"], "nosniff"); self.assertIn("no-store", r["Cache-Control"])
        self.assertTrue(r.content.startswith("\ufeff".encode())); self.assertIn(b"\r\n", r.content)

    def test_every_list_can_export_and_viewers_cannot(self):
        urls = {"/properties/": "properties", "/properties/units/": "units", "/inventory/": "inventory", "/contacts/": "contacts",
                "/maintenance/tasks/": "tasks", "/maintenance/?status=": "history", "/planner/events/?when=": "events",
                "/files/": "attachments", "/activity/": "activity"}
        for url, key in urls.items():
            sep = "&" if "?" in url else "?"
            self.login(self.alice)
            self.assertEqual(self.table(url + sep + "export=csv")[0], EXPECTED_HEADERS[key], url)
            self.login(self.vic)
            self.assertEqual(self.client.get(url + sep + "export=csv").status_code, 403 if key != "attachments" else 403, url)

    def test_maintenance_history_export_follows_the_status_filter(self):
        open_only = self.table("/maintenance/?export=csv")[1]
        everything = self.table("/maintenance/?status=&export=csv")[1]
        self.assertEqual({r[4] for r in open_only}, {"Open"}); self.assertEqual({r[4] for r in everything}, {"Open", "Done"})

    def test_button_appears_for_writers_only_and_keeps_the_filters(self):
        self.login(self.alice); r = self.client.get("/inventory/?category=hvac")
        self.assertContains(r, 'href="?category=hvac&amp;export=csv"')
        self.assertContains(self.client.get("/inventory/"), 'href="?export=csv"')
        self.login(self.vic); self.assertNotContains(self.client.get("/inventory/"), "Export CSV")

    def test_anonymous_gets_nothing(self):
        self.client.logout(); r = self.client.get("/inventory/?export=csv")
        self.assertEqual(r.status_code, 302); self.assertIn("/accounts/login/", r["Location"])


class HubAndZipTests(RichData):
    def test_hub_access_and_counts(self):
        r = self.client.get("/exports/")
        self.assertEqual(r.status_code, 200)
        counts = {d.key: n for d, n in r.context["datasets"]}
        self.assertEqual((counts["properties"], counts["contacts"], counts["events"], counts["attachments"]), (2, 1, 2, 1))
        for key in DATASETS:
            self.assertContains(r, f'href="/exports/{key}.csv"')
        mgr = User.objects.create_user("mgr@acme.test", PW); Membership.objects.create(user=mgr, account=self.acme, role="manager")
        self.login(mgr); self.assertEqual(self.client.get("/exports/").status_code, 200)
        self.login(self.vic)
        for url in ("/exports/", "/exports/properties.csv", "/exports/all.zip"):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.client.logout(); self.assertEqual(self.client.get("/exports/").status_code, 302)

    def test_unknown_dataset_is_404(self):
        for key in ("nope", "PROPERTIES", "properties.csv"):
            self.assertEqual(self.client.get(f"/exports/{key}.csv").status_code, 404, key)

    def test_zip_has_every_dataset_a_readme_and_matching_counts(self):
        with use_account(self.acme):
            self.oak.archive()
        r = self.client.get("/exports/all.zip")
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "application/zip"))
        self.assertRegex(r["Content-Disposition"], r'filename="all-data-acme-rentals-\d{4}-\d\d-\d\d\.zip"')
        files = zip_members(r)
        self.assertEqual(sorted(files), sorted([f"{k}.csv" for k in DATASETS] + ["README.txt"]))
        readme = files["README.txt"]
        self.assertIn("Acme Rentals (UTC)", readme)
        for d in DATASETS.values():
            rows = parse(files[f"{d.key}.csv"])
            self.assertEqual(rows[0], EXPECTED_HEADERS[d.key]); self.assertIn(f"{d.title}: {len(rows) - 1} rows", readme)
        self.assertIn("Oak Cottage", files["properties.csv"])                              # archived rows are in the bundle
        self.assertNotIn("Lakeview", "".join(files.values()))

    def test_oversized_exports_are_refused_not_truncated(self):
        with mock.patch("exports.responses.MAX_ROWS", 1):
            for url in ("/exports/properties.csv", "/exports/all.zip", "/properties/?export=csv"):
                r = self.client.get(url)
                self.assertEqual(r.status_code, 413, url); self.assertIn(b"more than 1 rows", r.content)
        self.assertFalse(ActivityLog.all_tenants.filter(action="exported").exists(), "a refused export is not an export")
        with mock.patch("exports.responses.MAX_ROWS", 2):
            self.assertEqual(self.client.get("/exports/properties.csv").status_code, 200)    # exactly at the cap is fine


class ExportAuditTests(RichData):
    def test_exports_are_recorded_with_row_counts(self):
        self.client.get("/inventory/?export=csv"); self.client.get("/exports/contacts.csv"); self.client.get("/exports/all.zip")
        with use_account(self.acme):
            entries = list(ActivityLog.objects.filter(action="exported").order_by("id"))
        self.assertEqual([e.summary for e in entries[:2]], ["Exported inventory (2 rows) as CSV", "Exported contacts (1 row) as CSV"])
        self.assertRegex(entries[2].summary, r"^Exported all data \(\d+ rows in 9 files\) as a zip$")
        self.assertEqual({(e.target_type, e.actor_email) for e in entries}, {("export", "alice@acme.test")})

    def test_the_export_shows_up_in_the_activity_feed(self):
        self.client.get("/exports/properties.csv")
        self.assertContains(self.client.get("/activity/?action=exported"), "Exported properties (2 rows) as CSV")


class FilenameAndTimezoneTests(RichData):
    def test_account_names_become_safe_ascii_slugs(self):
        for name, slug in (("Ünïcode / Rentals", "unicode-rentals"), ("../../etc/passwd", "etcpasswd"), ('Bob "The Builder" & Sons', "bob-the-builder-sons"),
                           ("///", "account"), ("日本語", "account")):
            Account.objects.filter(pk=self.acme.pk).update(name=name)
            r = self.client.get("/exports/properties.csv")
            self.assertRegex(r["Content-Disposition"], rf'^attachment; filename="properties-{re.escape(slug)}-\d{{4}}-\d\d-\d\d\.csv"$', name)

    def test_the_date_in_the_name_is_the_accounts_local_date(self):
        Account.objects.filter(pk=self.acme.pk).update(timezone="Pacific/Kiritimati")
        real = datetime.now(dt_timezone.utc).date()
        with mock.patch("django.utils.timezone.now", return_value=datetime.combine(real, T(20, 0), tzinfo=dt_timezone.utc)):
            r = self.client.get("/exports/properties.csv")
        self.assertIn(f"-{real + timedelta(days=1):%Y-%m-%d}.csv", r["Content-Disposition"])       # +14: already tomorrow


class InjectionThroughTheAppTests(ExportWorld):
    def test_hostile_values_entered_in_the_app_come_out_neutralised(self):
        self.login(self.alice)
        with use_account(self.acme):
            p = Property.objects.create(name='=HYPERLINK("http://evil.example","click")', notes="@SUM(1+1)*cmd|' /C calc'!A0")
            self.item(self.acme, p, "-2+3", notes="+cmd|' /C calc'!A0", cost=Decimal("-5.00"))
            Contact.objects.create(name="Ann", phone="+1 801-555-0142", email="a@b.example", notes="=1+1")
        head, rows = self.table("/exports/properties.csv")
        evil = next(r for r in rows if "HYPERLINK" in r[0])
        self.assertEqual(evil[0][0], "'"); self.assertEqual(evil[7][0], "'")
        inv = {r[0]: r for r in self.table("/exports/inventory.csv")[1]}
        self.assertEqual(inv["'-2+3"][10][0], "'"); self.assertEqual(inv["'-2+3"][7], "-5.00")        # text quoted, number intact
        ann = next(r for r in self.table("/exports/contacts.csv")[1] if r[0] == "Ann")
        self.assertEqual((ann[3], ann[6]), ("+1 801-555-0142", "'=1+1"))                                  # real phone number untouched
        # nothing in any dataset starts with a formula character unless it's a plain number
        from exports.responses import PLAIN_NUMBER
        for key in DATASETS:
            for row in parse(self.client.get(f"/exports/{key}.csv")):
                for value in row:
                    self.assertFalse(value.startswith(("=", "@", "\t", "\r")), (key, value))
                    self.assertFalse(value.startswith(("+", "-")) and value != "-" and not PLAIN_NUMBER.fullmatch(value), (key, value))
