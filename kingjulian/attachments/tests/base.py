"""Real files (made with Pillow / reportlab / python-docx / openpyxl) and a throwaway MEDIA_ROOT."""
import io
import os
import shutil
import tempfile
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image

from planner.tests.base import PlannerWorld


def _image(fmt, size=(64, 48), color=(200, 80, 40)):
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, fmt)
    return b.getvalue()


def png(**kw): return _image("PNG", **kw)
def jpeg(**kw): return _image("JPEG", **kw)
def gif(**kw): return _image("GIF", **kw)
def webp(**kw): return _image("WEBP", **kw)
def heic(): return b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00heicmif1" + b"\x00" * 64


def pdf(text="Boiler invoice $640.00"):
    from reportlab.pdfgen import canvas
    b = io.BytesIO()
    c = canvas.Canvas(b)
    c.drawString(72, 720, text)
    c.save()
    return b.getvalue()


def docx(text="Lease addendum"):
    import docx as _docx
    b = io.BytesIO()
    d = _docx.Document()
    d.add_paragraph(text)
    d.save(b)
    return b.getvalue()


def xlsx():
    import openpyxl
    b = io.BytesIO()
    w = openpyxl.Workbook()
    w.active["A1"] = "rent"
    w.save(b)
    return b.getvalue()


def zipped(names):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n in names:
            z.writestr(n, "x")
    return b.getvalue()


GOOD = [  # (filename, bytes, kind, inline)
    ("boiler.jpg", jpeg(), "jpeg", True), ("BOILER.JPEG", jpeg(), "jpeg", True), ("damage.png", png(), "png", True),
    ("anim.gif", gif(), "gif", True), ("kitchen.webp", webp(), "webp", True), ("IMG_0042.HEIC", heic(), "heic", False),
    ("invoice.pdf", pdf(), "pdf", False), ("lease.docx", docx(), "docx", False), ("rent roll.xlsx", xlsx(), "xlsx", False),
    ("notes.txt", "Notes — café\n".encode(), "text", False), ("export.csv", b"a,b\n1,2\n", "csv", False),
    ("ansi.csv", "name,city\nJosé,Zürich\n".encode("cp1252"), "csv", False),
]
HOSTILE = [  # things an attacker (or a confused user) might upload
    ("evil.html", b"<script>alert(1)</script>"), ("evil.htm", b"<html><body onload=alert(1)>"),
    ("evil.jpg", b"<html><script>alert(1)</script></html>"), ("logo.svg", b"<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'/>"),
    ("run.exe", b"MZ\x90\x00"), ("a.php.jpg", b"<?php system($_GET[0]); ?>"), ("shell.php", b"<?php echo 1;"),
    ("noext", png()), ("photo.png", jpeg()), ("fake.pdf", b"not a pdf"), ("fake.docx", zipped(["a.txt"])),
    ("fake.xlsx", zipped(["[Content_Types].xml", "word/document.xml"])), ("archive.zip", zipped(["a.txt"])),
    ("bin.txt", b"hello\x00world"), ("empty.png", b""), ("script.js", b"alert(1)"), ("doc.docx", b"PK\x03\x04 truncated"),
    ("pdf.pdf.exe", b"%PDF-1.4"), (".htaccess", b"AddType application/x-httpd-php .jpg"), ("x.txt", b"\xff\xfe\x00\x00\x81\x8d"),
]


def upload(name, data, content_type="application/octet-stream"):
    return SimpleUploadedFile(name, data, content_type=content_type)


class FilesWorld(PlannerWorld):
    def _pre_setup(self):
        """A fresh MEDIA_ROOT for every test (runs even when a subclass defines its own setUp)."""
        super()._pre_setup()
        self.media = tempfile.mkdtemp(prefix="propmgr-test-media-")
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, self.media, True)

    def disk(self):
        """Every stored file, relative to MEDIA_ROOT."""
        found = []
        for root, _, files in os.walk(self.media):
            found += [os.path.relpath(os.path.join(root, f), self.media) for f in files]
        return sorted(found)

    def post_file(self, kind, pk, name, data, caption="", user=None, content_type="application/octet-stream", **extra):
        if user:
            self.login(user)
        payload = {"file": upload(name, data, content_type), "caption": caption, **extra.pop("fields", {})}
        return self.client.post(f"/files/upload/{kind}/{pk}/", payload, **extra)

    def store(self, target, name="photo.png", data=None, account=None, user=None, caption=""):
        from attachments.services import store_attachment
        from core.tenancy import use_account
        with use_account(account or self.acme):
            return store_attachment(target=target, uploaded=upload(name, data if data is not None else png()),
                                    user=user or self.alice, caption=caption)
