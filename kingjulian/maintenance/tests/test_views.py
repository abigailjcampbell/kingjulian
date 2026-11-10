from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from core.tenancy import use_account
from core.tests.base import World
from maintenance.models import MaintenanceTask, TaskOccurrence
from maintenance.services import complete_occurrence, ensure_first_occurrence

TODAY = timezone.localdate()


def days(n):
    return TODAY + timedelta(days=n)


class ViewBase(World):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        with use_account(cls.acme):
            cls.late = MaintenanceTask.objects.create(
                property=cls.maple, unit=cls.maple_a, item=cls.heater, title="Flush water heater",
                start_date=days(-10), recurrence_rule="FREQ=YEARLY", assigned_contact=cls.plumber)
            cls.soon = MaintenanceTask.objects.create(
                property=cls.oak, title="Change furnace filter", start_date=days(5),
                recurrence_rule="FREQ=MONTHLY;INTERVAL=3")
            for t in (cls.late, cls.soon):
                ensure_first_occurrence(t)
            cls.late_occ = TaskOccurrence.objects.get(task=cls.late)
            cls.soon_occ = TaskOccurrence.objects.get(task=cls.soon)
        with use_account(cls.beta):
            cls.beta_task = MaintenanceTask.objects.create(
                property=cls.lake, title="Beta chiller service", start_date=days(3), recurrence_rule="FREQ=YEARLY")
            ensure_first_occurrence(cls.beta_task)
            cls.beta_occ = TaskOccurrence.objects.get(task=cls.beta_task)

    def setUp(self):
        self.login(self.alice)

    def task_post(self, **over):
        data = {"title": "Test sump pump", "property": self.maple.pk, "start_date": days(14).isoformat(),
                "repeat": "FREQ=MONTHLY", "schedule_mode": "fixed", "remind_days": "7, 1"}
        data.update(over)
        return self.client.post("/maintenance/tasks/new/", data)


class TodoListTests(ViewBase):
    def test_default_view_shows_open_work_soonest_first_with_countdown(self):
        r = self.client.get("/maintenance/")
        body = r.content.decode()
        self.assertEqual(r.status_code, 200)
        self.assertLess(body.index("Flush water heater"), body.index("Change furnace filter"))   # overdue first
        self.assertIn("10 days overdue", body); self.assertIn("In 5 days", body)
        self.assertNotIn("+ New", body)

    def test_status_filter_defaults_to_open_but_all_is_reachable(self):
        with use_account(self.acme):
            complete_occurrence(self.soon_occ, completed_on=TODAY)
        open_rows = [o.task.title for o in self.client.get("/maintenance/").context["object_list"]]
        done_rows = [o.status for o in self.client.get("/maintenance/?status=done").context["object_list"]]
        all_rows = [o.status for o in self.client.get("/maintenance/?status=").context["object_list"]]
        self.assertEqual(sorted(open_rows), ["Change furnace filter", "Flush water heater"])   # soon's NEXT one is open
        self.assertEqual(done_rows, ["done"])
        self.assertEqual(sorted(all_rows), ["done", "open", "open"])

    def test_filter_by_property_and_search(self):
        titles = lambda qs: [o.task.title for o in self.client.get("/maintenance/?" + qs).context["object_list"]]
        self.assertEqual(titles(f"property={self.oak.pk}"), ["Change furnace filter"])
        self.assertEqual(titles("q=heater"), ["Flush water heater"])
        self.assertEqual(titles("q=plumber"), [])          # search is task/property/item/notes, not contacts

    def test_other_tenants_work_is_invisible(self):
        for url in ("/maintenance/", "/maintenance/?status=", "/maintenance/tasks/"):
            self.assertNotIn("Beta chiller", self.client.get(url).content.decode(), url)

    def test_archived_task_disappears_from_todo_list(self):
        self.client.post(f"/maintenance/tasks/{self.soon.pk}/archive/")
        titles = [o.task.title for o in self.client.get("/maintenance/?status=").context["object_list"]]
        self.assertNotIn("Change furnace filter", titles)


class TaskFormTests(ViewBase):
    def test_create_task_opens_its_first_occurrence_and_stores_reminder_days(self):
        r = self.task_post()
        self.assertRedirects(r, "/maintenance/tasks/")
        with use_account(self.acme):
            t = MaintenanceTask.objects.get(title="Test sump pump")
            occ = list(TaskOccurrence.objects.filter(task=t))
        self.assertEqual((t.recurrence_rule, t.remind_days_before, t.account_id), ("FREQ=MONTHLY", [7, 1], self.acme.pk))
        self.assertEqual([(o.due_date, o.status) for o in occ], [(days(14), "open")])

    def test_custom_rule_accepted_and_normalised(self):
        self.task_post(repeat="__custom__", custom_rule="freq=weekly;byday=mo,th")
        with use_account(self.acme):
            self.assertEqual(MaintenanceTask.objects.get(title="Test sump pump").recurrence_rule,
                             "FREQ=WEEKLY;BYDAY=MO,TH")

    def test_bad_inputs_are_form_errors_not_500s(self):
        cases = [(dict(repeat="__custom__", custom_rule="FREQ=HOURLY"), "custom_rule"),
                 (dict(repeat="__custom__", custom_rule=""), "custom_rule"),
                 (dict(repeat="__custom__", custom_rule="banana"), "custom_rule"),
                 (dict(remind_days="a, b"), "remind_days"),
                 (dict(remind_days="999"), "remind_days"),
                 (dict(start_date="not-a-date"), "start_date")]
        for over, field in cases:
            r = self.task_post(**over)
            self.assertEqual(r.status_code, 200, over)
            self.assertTrue(r.context["form"].errors.get(field), over)
        with use_account(self.acme):
            self.assertFalse(MaintenanceTask.objects.filter(title="Test sump pump").exists())

    def test_remind_days_parsing(self):
        for text, want in (("7, 1, 1", [7, 1]), ("1 7 14", [14, 7, 1]), ("", []), ("0", [0])):
            self.task_post(title=f"t{text}", remind_days=text)
            with use_account(self.acme):
                self.assertEqual(MaintenanceTask.objects.get(title=f"t{text}").remind_days_before, want, text)

    def test_forged_foreign_ids_and_mismatched_links_rejected(self):
        for over, field in ((dict(property=self.lake.pk), "property"),
                            (dict(item=self.beta_item.pk), "item"),
                            (dict(assigned_contact=self.beta_contact.pk), "assigned_contact"),
                            (dict(unit=self.lake_unit.pk), "unit"),
                            (dict(item=self.fridge.pk), "item"),                       # item belongs to Oak, task to Maple
                            (dict(unit=self.maple_b.pk, item=self.heater.pk), "item")):  # heater is in Unit A
            r = self.task_post(**over)
            self.assertEqual(r.status_code, 200, over)
            self.assertTrue(r.context["form"].errors.get(field), over)
        with use_account(self.acme):
            self.assertFalse(MaintenanceTask.objects.filter(title="Test sump pump").exists())

    def test_edit_prepopulates_and_preserves_schedule(self):
        r = self.client.get(f"/maintenance/tasks/{self.soon.pk}/")
        f = r.context["form"]
        self.assertEqual((f.fields["repeat"].initial, f.fields["remind_days"].initial),
                         ("FREQ=MONTHLY;INTERVAL=3", "7, 1"))
        self.client.post(f"/maintenance/tasks/{self.soon.pk}/", {
            "title": "Change furnace filter (MERV 13)", "property": self.oak.pk, "start_date": days(5).isoformat(),
            "repeat": "FREQ=MONTHLY;INTERVAL=3", "schedule_mode": "fixed", "remind_days": "7, 1"})
        with use_account(self.acme):
            self.assertEqual(MaintenanceTask.objects.get(pk=self.soon.pk).title, "Change furnace filter (MERV 13)")
            self.assertEqual(TaskOccurrence.objects.filter(task=self.soon).count(), 1)   # no duplicate occurrence

    def test_task_list_shows_next_due_and_sorts_by_it(self):
        r = self.client.get("/maintenance/tasks/")
        titles = [t.title for t in r.context["object_list"]]
        self.assertEqual(titles, ["Flush water heater", "Change furnace filter"])
        self.assertContains(r, "Yearly"); self.assertContains(r, "Every 3 months")

    def test_other_tenants_task_is_404(self):
        self.assertEqual(self.client.get(f"/maintenance/tasks/{self.beta_task.pk}/").status_code, 404)
        self.assertEqual(self.client.post(f"/maintenance/tasks/{self.beta_task.pk}/archive/").status_code, 404)


class OccurrencePageTests(ViewBase):
    def test_open_occurrence_page_renders_for_owner_with_actions(self):
        r = self.client.get(f"/maintenance/{self.late_occ.pk}/")
        self.assertEqual(r.status_code, 200)
        for needle in ("Flush water heater", "Maple Duplex", "Water Heater 50gal", "Yearly", "Dana Pipes",
                       "10 days overdue", "Mark complete", "Skip this time", "Edit task", ">Save<"):
            self.assertContains(r, needle)
        self.assertNotContains(r, "Archive")

    def test_closed_occurrence_page_shows_history_and_no_actions(self):
        with use_account(self.acme):
            complete_occurrence(self.soon_occ, completed_on=TODAY, cost=Decimal("40"))
        r = self.client.get(f"/maintenance/{self.soon_occ.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Completed"); self.assertNotContains(r, "Mark complete")
        self.assertTrue(r.context["form"].fields["due_date"].disabled)
        self.assertIn("cost", r.context["form"].fields)

    def test_every_page_in_the_app_renders_for_the_owner(self):
        urls = ["/maintenance/", "/maintenance/?status=", "/maintenance/tasks/", "/maintenance/tasks/new/",
                f"/maintenance/tasks/{self.late.pk}/", f"/maintenance/{self.late_occ.pk}/",
                f"/maintenance/{self.late_occ.pk}/complete/"]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)


class CompleteAndSkipTests(ViewBase):
    def test_complete_flow_records_cost_and_opens_next(self):
        url = f"/maintenance/{self.late_occ.pk}/complete/"
        self.assertEqual(self.client.get(url).status_code, 200)
        r = self.client.post(url, {"completed_on": TODAY.isoformat(), "cost": "120.50", "notes": "Flushed, anode ok"})
        self.assertRedirects(r, "/maintenance/", fetch_redirect_response=False)   # fetching would eat the flash message
        with use_account(self.acme):
            done = TaskOccurrence.objects.get(pk=self.late_occ.pk)
            nxt = TaskOccurrence.objects.get(task=self.late, status="open")
        self.assertEqual((done.status, done.cost, done.completed_by), ("done", Decimal("120.50"), self.alice))
        self.assertGreater(nxt.due_date, TODAY)
        self.assertContains(self.client.get("/maintenance/"), "Completed Flush water heater.")

    def test_double_submit_is_harmless(self):
        url = f"/maintenance/{self.late_occ.pk}/complete/"
        data = {"completed_on": TODAY.isoformat()}
        self.client.post(url, data); self.client.post(url, data)
        with use_account(self.acme):
            self.assertEqual(TaskOccurrence.objects.filter(task=self.late).count(), 2)

    def test_future_completion_date_rejected(self):
        r = self.client.post(f"/maintenance/{self.late_occ.pk}/complete/", {"completed_on": days(3).isoformat()})
        self.assertTrue(r.context["form"].errors.get("completed_on"))
        with use_account(self.acme):
            self.assertEqual(TaskOccurrence.objects.get(pk=self.late_occ.pk).status, "open")

    def test_skip_advances_the_schedule(self):
        r = self.client.post(f"/maintenance/{self.soon_occ.pk}/skip/")
        self.assertRedirects(r, "/maintenance/")
        with use_account(self.acme):
            self.assertEqual(TaskOccurrence.objects.get(pk=self.soon_occ.pk).status, "skipped")
            self.assertEqual(TaskOccurrence.objects.filter(task=self.soon, status="open").count(), 1)

    def test_skip_requires_post(self):
        self.assertEqual(self.client.get(f"/maintenance/{self.soon_occ.pk}/skip/").status_code, 405)

    def test_viewer_can_look_but_not_act(self):
        self.login(self.vic)
        self.assertEqual(self.client.get("/maintenance/").status_code, 200)
        self.assertEqual(self.client.get(f"/maintenance/{self.late_occ.pk}/").status_code, 200)
        for method, url in (("get", f"/maintenance/{self.late_occ.pk}/complete/"),
                            ("post", f"/maintenance/{self.late_occ.pk}/complete/"),
                            ("post", f"/maintenance/{self.late_occ.pk}/skip/"),
                            ("get", "/maintenance/tasks/new/"), ("post", f"/maintenance/tasks/{self.late.pk}/archive/")):
            self.assertEqual(getattr(self.client, method)(url, {"completed_on": TODAY.isoformat()}).status_code, 403, url)
        self.assertEqual(self.client.post(f"/maintenance/{self.late_occ.pk}/", {"due_date": days(40).isoformat()}).status_code, 403)
        page = self.client.get(f"/maintenance/{self.late_occ.pk}/")
        self.assertNotContains(page, "Mark complete"); self.assertNotContains(page, ">Save<")
        self.assertTrue(all(f.disabled for f in page.context["form"].fields.values()))
        self.assertContains(self.client.get("/maintenance/"), f'href="/maintenance/{self.late_occ.pk}/"')   # rows are links for viewers here
        with use_account(self.acme):
            self.assertEqual(TaskOccurrence.objects.get(pk=self.late_occ.pk).status, "open")

    def test_other_tenants_occurrence_is_404_everywhere(self):
        for method, url in (("get", f"/maintenance/{self.beta_occ.pk}/"),
                            ("get", f"/maintenance/{self.beta_occ.pk}/complete/"),
                            ("post", f"/maintenance/{self.beta_occ.pk}/complete/"),
                            ("post", f"/maintenance/{self.beta_occ.pk}/skip/")):
            self.assertEqual(getattr(self.client, method)(url, {"completed_on": TODAY.isoformat()}).status_code, 404, url)
        with use_account(self.beta):
            self.assertEqual(TaskOccurrence.objects.get(pk=self.beta_occ.pk).status, "open")

    def test_reschedule_open_occurrence_and_history_date_is_locked(self):
        self.client.post(f"/maintenance/{self.soon_occ.pk}/", {"due_date": days(9).isoformat(), "notes": "moved"})
        with use_account(self.acme):
            o = TaskOccurrence.objects.get(pk=self.soon_occ.pk)
        self.assertEqual((o.due_date, o.notes), (days(9), "moved"))
        with use_account(self.acme):
            complete_occurrence(o, completed_on=TODAY)
        self.client.post(f"/maintenance/{o.pk}/", {"due_date": days(99).isoformat(), "cost": "55", "notes": "paid"})
        with use_account(self.acme):
            o = TaskOccurrence.objects.get(pk=o.pk)
        self.assertEqual((o.due_date, o.cost), (days(9), Decimal("55.00")))     # due date ignored, cost editable
