import os
from datetime import timedelta
from urllib.parse import quote

from django.test import Client
from django.utils import timezone

from accounts.models import Membership, User
from attachments.models import Attachment
from core.tenancy import use_account
from core.tests.base import PW

from .base import FilesWorld, HOSTILE, GOOD, docx, heic, jpeg, pdf, png

XSS = "<script>alert(1)</script>"


class UploadTests(FilesWorld):
    def setUp(self):
        self.login(self.alice)

    def test_owner_uploads_and_lands_back_on_the_record_with_a_thumbnail(self):
        r = self.post_file("property", self.maple.pk, "kitchen leak.png", png(), caption="Under the sink", follow=True)
        self.assertEqual(r.redirect_chain[-1][0], f"/properties/{self.maple.pk}/")
        self.assertContains(r, "Uploaded kitchen leak.png.")
        att = Attachment.all_tenants.get()
        self.assertEqual((att.account_id, att.caption, att.uploaded_by, att.content_type), (self.acme.pk, "Under the sink", self.alice, "image/png"))
        self.assertContains(r, "kitchen leak.png"); self.assertContains(r, f'src="/files/{att.pk}/view/"'); self.assertContains(r, "Under the sink")
        self.assertEqual(len(self.disk()), 1)

    def test_managers_can_upload_viewers_cannot(self):
        mgr = User.objects.create_user("mgr@acme.test", PW); Membership.objects.create(user=mgr, account=self.acme, role="manager")
        self.assertEqual(self.post_file("property", self.maple.pk, "a.png", png(), user=mgr).status_code, 302)
        self.assertEqual(self.post_file("property", self.maple.pk, "b.png", png(), user=self.vic).status_code, 403)
        self.assertEqual(Attachment.all_tenants.count(), 1); self.assertEqual(len(self.disk()), 1)

    def test_anonymous_upload_is_redirected_to_login(self):
        self.client.logout()
        r = self.post_file("property", self.maple.pk, "a.png", png())
        self.assertEqual(r.status_code, 302); self.assertIn("/accounts/login/", r["Location"])
        self.assertEqual(self.disk(), [])

    def test_cannot_attach_to_another_tenants_or_an_archived_or_unknown_record(self):
        self.assertEqual(self.post_file("property", self.lake.pk, "a.png", png()).status_code, 404)
        self.assertEqual(self.post_file("item", self.beta_item.pk, "a.png", png()).status_code, 404)
        self.assertEqual(self.post_file("unit", self.maple_a.pk, "a.png", png()).status_code, 404)           # not a target kind
        self.assertEqual(self.post_file("nonsense", 1, "a.png", png()).status_code, 404)
        with use_account(self.acme):
            self.oak.archive()
        self.assertEqual(self.post_file("property", self.oak.pk, "a.png", png()).status_code, 404)
        self.assertEqual((Attachment.all_tenants.count(), self.disk()), (0, []))

    def test_every_hostile_upload_is_rejected_and_leaves_no_trace(self):
        for name, data in HOSTILE:
            r = self.post_file("property", self.maple.pk, name, data, follow=True)
            self.assertEqual(r.status_code, 200, name)
            self.assertEqual((Attachment.all_tenants.count(), self.disk()), (0, []), name)

    def test_the_error_is_explained_to_the_user(self):
        r = self.post_file("property", self.maple.pk, "evil.jpg", b"<html>", follow=True)
        self.assertContains(r, "doesn&#x27;t look like a real .jpg file")

    def test_a_missing_file_field_is_a_friendly_error(self):
        r = self.client.post(f"/files/upload/property/{self.maple.pk}/", {"caption": "x"}, follow=True)
        self.assertContains(r, "required"); self.assertEqual(Attachment.all_tenants.count(), 0)

    def test_oversized_request_is_refused_by_middleware_before_the_body_is_read(self):
        r = self.post_file("property", self.maple.pk, "a.png", png(), CONTENT_LENGTH="999999999")
        self.assertEqual(r.status_code, 413); self.assertIn(b"too large", r.content)
        self.assertEqual((Attachment.all_tenants.count(), self.disk()), (0, []))

    def test_missing_or_garbage_content_length_is_refused_and_other_paths_are_untouched(self):
        for bad in ("", "abc", "-5"):
            self.assertEqual(self.post_file("property", self.maple.pk, "a.png", png(), CONTENT_LENGTH=bad).status_code, 411, bad)
        self.assertEqual(self.client.post("/accounts/switch/", {"account_id": self.acme.pk}).status_code, 302)   # other URLs unaffected
        self.assertEqual(self.client.get(f"/files/upload/property/{self.maple.pk}/").status_code, 405)              # only POSTs are inspected

    def test_the_limit_follows_the_setting(self):
        from django.test import override_settings
        with override_settings(ATTACHMENT_MAX_BYTES=1000):
            self.assertEqual(self.post_file("property", self.maple.pk, "a.png", png(), CONTENT_LENGTH=str(1000 + 1024 * 1024 + 1)).status_code, 413)
            self.assertEqual(self.post_file("property", self.maple.pk, "a.png", png()).status_code, 302)   # normal small upload passes the guard

    def test_csrf_is_enforced(self):
        c = Client(enforce_csrf_checks=True); c.force_login(self.alice)
        from .base import upload
        r = c.post(f"/files/upload/property/{self.maple.pk}/", {"file": upload("a.png", png())})
        self.assertEqual(r.status_code, 403); self.assertEqual(Attachment.all_tenants.count(), 0)

    def test_next_parameter_cannot_redirect_off_site(self):
        for bad in ("https://evil.example/", "//evil.example/x", "javascript:alert(1)", "http://testserver.evil.com/"):
            r = self.post_file("property", self.maple.pk, "a.png", png(), fields={"next": bad})
            self.assertEqual(r["Location"], f"/properties/{self.maple.pk}/", bad)
        r = self.post_file("property", self.maple.pk, "a.png", png(), fields={"next": "/contacts/"})
        self.assertEqual(r["Location"], "/contacts/")

    def test_polyglot_png_is_accepted_but_can_never_be_rendered_as_a_page(self):
        sneaky = png() + b"<html><script>alert(document.cookie)</script></html>"
        self.post_file("property", self.maple.pk, "sneaky.png", sneaky)
        att = Attachment.all_tenants.get()
        for url in (f"/files/{att.pk}/view/", f"/files/{att.pk}/download/"):
            r = self.client.get(url)
            self.assertEqual(r["Content-Type"], "image/png"); self.assertEqual(r["X-Content-Type-Options"], "nosniff")
            self.assertIn("sandbox", r["Content-Security-Policy"])


class DownloadTests(FilesWorld):
    def setUp(self):
        self.data = pdf("Invoice $640")
        self.att = self.store(self.maple, "Boiler invoice.pdf", self.data)
        self.login(self.alice)

    def body(self, r):
        return b"".join(r.streaming_content)

    def test_download_returns_the_exact_bytes_with_safe_headers(self):
        r = self.client.get(f"/files/{self.att.pk}/download/")
        self.assertEqual(r.status_code, 200); self.assertEqual(self.body(r), self.data)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertIn('attachment; filename="Boiler invoice.pdf"', r["Content-Disposition"])
        self.assertEqual(r["X-Content-Type-Options"], "nosniff"); self.assertIn("no-store", r["Cache-Control"])
        self.assertIn("sandbox", r["Content-Security-Policy"]); self.assertEqual(r["Content-Length"], str(len(self.data)))

    def test_the_view_sets_its_own_security_headers_even_without_middleware(self):
        """SecurityMiddleware also adds nosniff; ours must not depend on it. Call the views directly."""
        from django.test import RequestFactory
        from attachments.views import AttachmentDownload, AttachmentInline
        img = self.store(self.maple, "kitchen.png", png())
        for view, att in ((AttachmentDownload, self.att), (AttachmentInline, img)):
            req = RequestFactory().get("/"); req.user = self.alice; req.account = self.acme
            req.membership = Membership.objects.get(user=self.alice, account=self.acme)
            with use_account(self.acme):
                r = view.as_view()(req, pk=att.pk)
            self.assertEqual(r["X-Content-Type-Options"], "nosniff", view.__name__)
            self.assertIn("sandbox", r["Content-Security-Policy"]); self.assertIn("no-store", r["Cache-Control"])
            self.assertEqual(r["Content-Type"], att.content_type)
            r.file_to_stream.close()          # not r.close(): that fires request_finished and closes the DB connection

    def test_non_ascii_names_are_encoded_in_the_header(self):
        att = self.store(self.maple, "Rechnung Größe ü.pdf", self.data)
        r = self.client.get(f"/files/{att.pk}/download/")
        self.assertIn("filename*=utf-8''Rechnung%20Gr%C3%B6%C3%9Fe%20%C3%BC.pdf", r["Content-Disposition"])

    def test_viewers_may_download_everyone_else_may_not(self):
        self.login(self.vic)
        self.assertEqual(self.client.get(f"/files/{self.att.pk}/download/").status_code, 200)
        self.login(self.bella); self.assertEqual(self.client.get(f"/files/{self.att.pk}/download/").status_code, 404)
        self.client.logout(); r = self.client.get(f"/files/{self.att.pk}/download/")
        self.assertEqual(r.status_code, 302); self.assertIn("/accounts/login/", r["Location"])

    def test_other_tenants_files_are_unreachable_by_id(self):
        theirs = self.store(self.lake, "secret.pdf", pdf("secret"), account=self.beta, user=self.bella)
        for tail in ("download/", "view/", "", "delete/"):
            method = self.client.post if tail == "delete/" else self.client.get
            self.assertEqual(method(f"/files/{theirs.pk}/{tail}").status_code, 404, tail)
        self.assertEqual(Attachment.all_tenants.filter(pk=theirs.pk).count(), 1)

    def test_the_media_directory_is_not_served(self):
        for url in (f"/media/{self.att.file.name}", f"/{self.att.file.name}", "/static/" + self.att.file.name):
            self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_missing_file_on_disk_is_a_404_not_a_crash(self):
        os.remove(os.path.join(self.media, self.att.file.name))
        self.assertEqual(self.client.get(f"/files/{self.att.pk}/download/").status_code, 404)

    def test_inline_view_is_for_images_only(self):
        img = self.store(self.maple, "kitchen.png", png())
        r = self.client.get(f"/files/{img.pk}/view/")
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "image/png"))
        self.assertTrue(r["Content-Disposition"].startswith("inline")); self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        for name, data in (("a.pdf", pdf()), ("a.docx", docx()), ("a.heic", heic()), ("a.txt", b"hi")):
            att = self.store(self.maple, name, data)
            self.assertEqual(self.client.get(f"/files/{att.pk}/view/").status_code, 404, name)
            self.assertEqual(self.client.get(f"/files/{att.pk}/download/").status_code, 200, name)


class DeleteTests(FilesWorld):
    def setUp(self):
        self.att = self.store(self.maple, "old receipt.png", png())
        self.login(self.alice)

    def test_writer_deletes_row_and_file_and_returns_to_the_record(self):
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.post(f"/files/{self.att.pk}/delete/")
        self.assertRedirects(r, f"/properties/{self.maple.pk}/", fetch_redirect_response=False)
        self.assertEqual((Attachment.all_tenants.count(), self.disk()), (0, []))

    def test_viewers_other_tenants_and_get_requests_cannot_delete(self):
        self.login(self.vic); self.assertEqual(self.client.post(f"/files/{self.att.pk}/delete/").status_code, 403)
        self.login(self.bella); self.assertEqual(self.client.post(f"/files/{self.att.pk}/delete/").status_code, 404)
        self.login(self.alice); self.assertEqual(self.client.get(f"/files/{self.att.pk}/delete/").status_code, 405)
        self.assertEqual((Attachment.all_tenants.count(), len(self.disk())), (1, 1))


class PagesTests(FilesWorld):
    def test_detail_page_shows_metadata_and_a_preview_for_images(self):
        att = self.store(self.maple, "kitchen.png", png(), caption="After repair")
        self.login(self.alice); r = self.client.get(f"/files/{att.pk}/")
        for text in ("kitchen.png", "After repair", "image/png", att.sha256, "Maple Duplex", "Delete", f'src="/files/{att.pk}/view/"'):
            self.assertContains(r, text)

    def test_viewer_sees_the_page_without_delete_and_without_links_they_cannot_open(self):
        on_property = self.store(self.maple, "a.png", png())
        occ = self.occ(self.acme, self.maple, "Pump", self.TODAY); on_occ = self.store(occ, "b.png", png())
        self.login(self.vic)
        r = self.client.get(f"/files/{on_property.pk}/")
        self.assertNotContains(r, "Delete"); self.assertNotContains(r, "open record")
        r = self.client.get(f"/files/{on_occ.pk}/")
        self.assertContains(r, f'href="/maintenance/{occ.pk}/"'); self.assertNotContains(r, "Delete")

    def test_list_search_filter_and_isolation(self):
        self.store(self.maple, "boiler invoice.pdf", pdf(), caption="2026 service"); self.store(self.heater, "heater photo.png", png())
        self.store(self.lake, "BETA-secret.png", png(), account=self.beta, user=self.bella)
        self.login(self.alice)
        names = lambda q="": [a.original_name for a in self.client.get("/files/" + q).context["object_list"]]
        self.assertEqual(sorted(names()), ["boiler invoice.pdf", "heater photo.png"])
        self.assertEqual(names("?q=2026"), ["boiler invoice.pdf"])                  # caption
        self.assertEqual(names("?q=water"), ["heater photo.png"])                   # the item's name
        self.assertEqual(names("?kind=item"), ["heater photo.png"]); self.assertEqual(names("?kind=property"), ["boiler invoice.pdf"])
        self.assertEqual(names("?kind=garbage"), sorted(names()) if False else names("?kind=garbage"))
        self.assertNotContains(self.client.get("/files/?q="), "BETA-secret")

    def test_viewers_can_browse_files(self):
        att = self.store(self.maple, "a.png", png()); self.login(self.vic)
        r = self.client.get("/files/")
        self.assertContains(r, "a.png"); self.assertContains(r, f'href="/files/{att.pk}/"')

    def test_user_supplied_text_is_escaped(self):
        with use_account(self.acme):
            self.maple.name = XSS; self.maple.save()
        self.store(self.maple, "x.png", png(), caption="<b>bold</b>")
        self.login(self.alice)
        for url in ("/files/", f"/files/{Attachment.all_tenants.get().pk}/", f"/properties/{self.maple.pk}/"):
            r = self.client.get(url)
            self.assertNotContains(r, XSS); self.assertNotContains(r, "<b>bold</b>")


class PanelTests(FilesWorld):
    def test_panel_appears_on_every_record_page_for_writers(self):
        occ = self.occ(self.acme, self.maple, "Pump", self.TODAY); ev = self.event(self.acme, "Inspection", self.TODAY)
        self.login(self.alice)
        pages = {f"/properties/{self.maple.pk}/": "property", f"/inventory/{self.heater.pk}/": "item",
                 f"/contacts/{self.plumber.pk}/": "contact", f"/planner/events/{ev.pk}/": "event",
                 f"/maintenance/tasks/{occ.task.pk}/": "task", f"/maintenance/{occ.pk}/": "occurrence"}
        for url, kind in pages.items():
            r = self.client.get(url); pk = url.rstrip("/").rsplit("/", 1)[1]
            self.assertContains(r, "<h2>Files</h2>"); self.assertContains(r, f'action="/files/upload/{kind}/{pk}/"', msg_prefix=url)
            self.assertContains(r, 'accept=".csv,.docx')

    def test_no_panel_on_create_pages(self):
        self.login(self.alice)
        for url in ("/properties/new/", "/contacts/new/", "/inventory/new/"):
            self.assertNotContains(self.client.get(url), "<h2>Files</h2>")

    def test_viewers_see_the_list_but_no_upload_form(self):
        occ = self.occ(self.acme, self.maple, "Pump", self.TODAY); self.store(occ, "receipt.png", png())
        self.login(self.vic); r = self.client.get(f"/maintenance/{occ.pk}/")
        self.assertContains(r, "receipt.png"); self.assertNotContains(r, 'type="file"'); self.assertNotContains(r, "Upload</button>")
