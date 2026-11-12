import time

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, override_settings

from attachments.validators import (ALLOWED_EXTENSIONS, human_size, inspect_upload, sanitize_filename)

from .base import GOOD, HOSTILE, docx, png, upload, zipped


class AcceptTests(SimpleTestCase):
    def test_real_files_are_accepted_and_classified_by_content(self):
        for name, data, kind, inline in GOOD:
            r = inspect_upload(upload(name, data))
            self.assertEqual((r.kind, r.inline), (kind, inline), name)

    def test_the_browsers_claimed_content_type_is_ignored(self):
        self.assertEqual(inspect_upload(upload("a.png", png(), "text/html")).content_type, "image/png")
        for name, data in (("a.html", b"<script>1</script>"), ("a.png", b"<script>1</script>")):
            with self.assertRaises(ValidationError, msg=name):
                inspect_upload(upload(name, data, "image/png"))

    def test_extension_handling(self):
        from .base import jpeg
        self.assertEqual(inspect_upload(upload("My.Photo.v2.JPG", jpeg())).kind, "jpeg")
        for bad in ("x.tar.gz", "photo.jpg.exe", "photo.", "photo", ".jpg"):
            with self.assertRaises(ValidationError, msg=bad):
                inspect_upload(upload(bad, jpeg()))

    def test_a_valid_zip_container_is_never_expanded(self):
        import io, zipfile
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", "x"); z.writestr("word/document.xml", "x")
            z.writestr("bomb.bin", b"\x00" * (200 * 1024 * 1024))       # ~200 KB on disk, 200 MB if expanded
        data = b.getvalue()
        self.assertLess(len(data), 1024 * 1024)
        t = time.time()
        self.assertEqual(inspect_upload(upload("big.docx", data)).kind, "docx")
        self.assertLess(time.time() - t, 1.0, "inspection must only read the zip directory")


class RejectTests(SimpleTestCase):
    def test_hostile_uploads_are_all_rejected_with_a_message(self):
        for name, data in HOSTILE:
            with self.assertRaises(ValidationError, msg=name) as ctx:
                inspect_upload(upload(name, data))
            self.assertTrue(ctx.exception.messages[0], name)

    def test_messages_distinguish_wrong_type_from_fake_content(self):
        with self.assertRaisesMessage(ValidationError, "isn't allowed"):
            inspect_upload(upload("a.exe", b"MZ"))
        with self.assertRaisesMessage(ValidationError, "doesn't look like a real .pdf"):
            inspect_upload(upload("a.pdf", b"hello"))
        with self.assertRaisesMessage(ValidationError, "empty"):
            inspect_upload(upload("a.png", b""))

    def test_svg_html_and_scripts_are_not_on_the_allow_list(self):
        for ext in ("svg", "html", "htm", "js", "php", "exe", "zip", "xml", "xhtml", "swf"):
            self.assertNotIn(ext, ALLOWED_EXTENSIONS)

    @override_settings(ATTACHMENT_MAX_BYTES=1000)
    def test_size_limit_and_message(self):
        inspect_upload(upload("ok.txt", b"x" * 1000))
        with self.assertRaisesMessage(ValidationError, "too large (limit 1000 bytes)"):
            inspect_upload(upload("big.txt", b"x" * 1001))

    def test_human_size(self):
        self.assertEqual([human_size(n) for n in (500, 2048, 10 * 1024 * 1024, 1536 * 1024)], ["500 bytes", "2 KB", "10 MB", "1.5 MB"])


class FilenameTests(SimpleTestCase):
    def test_sanitisation(self):
        cases = {"../../etc/passwd": "passwd", "C:\\Users\\bob\\inv.pdf": "inv.pdf", "inv\u202egpj.exe": "invgpj.exe",
                 "a\x00b\nc.txt": "abc.txt", "  spaced   out .pdf ": "spaced out .pdf", "...hidden.txt": "hidden.txt",
                 "": "file", None: "file", "<img src=x onerror=1>.png": "img src=x onerror=1.png",
                 'a"b|c?d*e:f.pdf': "abcdef.pdf", "con\u200bcealed.jpg": "concealed.jpg", "/": "file", "...": "file",
                 "Größe.pdf": "Größe.pdf", "日本語.pdf": "日本語.pdf"}
        for raw, want in cases.items():
            self.assertEqual(sanitize_filename(raw), want, repr(raw))

    def test_long_names_are_trimmed_but_keep_their_extension(self):
        out = sanitize_filename("x" * 400 + ".pdf")
        self.assertEqual((len(out), out.endswith(".pdf")), (150, True))
        self.assertEqual(len(sanitize_filename("y" * 400)), 150)

    def test_unicode_is_normalised(self):
        self.assertEqual(sanitize_filename("e\u0301.jpg"), "\u00e9.jpg")
