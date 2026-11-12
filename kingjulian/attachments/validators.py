"""Decide whether an upload is acceptable *by looking at its bytes*, never at what the browser claims.

Allow-list only. A file must (1) have an allowed extension and (2) actually look like that kind of file.
HTML, SVG, scripts, archives and executables are never accepted, so nothing we serve back can run in a browser.
"""
import re
import unicodedata
import zipfile
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError

DEFAULT_MAX_BYTES = 10 * 1024 * 1024
HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}


@dataclass(frozen=True)
class Kind:
    content_type: str
    exts: tuple
    inline: bool = False            # safe to render in the browser (images only)


KINDS = {
    "jpeg": Kind("image/jpeg", ("jpg", "jpeg"), True),
    "png": Kind("image/png", ("png",), True),
    "gif": Kind("image/gif", ("gif",), True),
    "webp": Kind("image/webp", ("webp",), True),
    "heic": Kind("image/heic", ("heic", "heif")),                      # iPhone photos; browsers can't show them
    "pdf": Kind("application/pdf", ("pdf",)),
    "docx": Kind("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ("docx",)),
    "xlsx": Kind("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ("xlsx",)),
    "text": Kind("text/plain", ("txt",)),
    "csv": Kind("text/csv", ("csv",)),
}
EXT_TO_KIND = {ext: name for name, k in KINDS.items() for ext in k.exts}
ALLOWED_EXTENSIONS = sorted(EXT_TO_KIND)


@dataclass(frozen=True)
class Inspected:
    kind: str
    ext: str
    content_type: str
    inline: bool


def human_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):g} MB"
    return f"{n / 1024:g} KB" if n >= 1024 else f"{n} bytes"


def max_bytes() -> int:
    return getattr(settings, "ATTACHMENT_MAX_BYTES", DEFAULT_MAX_BYTES)


def _head(uploaded, n=4096) -> bytes:
    uploaded.seek(0)
    data = uploaded.read(n)
    uploaded.seek(0)
    return data


def _zip_names(uploaded):
    uploaded.seek(0)
    try:
        names = zipfile.ZipFile(uploaded).namelist()     # reads the central directory only; nothing is extracted
    except (zipfile.BadZipFile, OSError, ValueError):
        names = None
    uploaded.seek(0)
    return names


def _looks_like(kind: str, head: bytes, uploaded) -> bool:
    if kind == "jpeg":
        return head.startswith(b"\xff\xd8\xff")
    if kind == "png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if kind == "gif":
        return head.startswith((b"GIF87a", b"GIF89a"))
    if kind == "webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if kind == "heic":
        return head[4:8] == b"ftyp" and head[8:12] in HEIC_BRANDS
    if kind == "pdf":
        return head.startswith(b"%PDF-")
    if kind in ("docx", "xlsx"):
        names = _zip_names(uploaded)
        marker = "word/document.xml" if kind == "docx" else "xl/workbook.xml"
        return bool(names) and "[Content_Types].xml" in names and marker in names
    if kind in ("text", "csv"):
        if b"\x00" in head:
            return False
        try:
            head.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                head.decode("cp1252")                         # old spreadsheets exported as ANSI
            except UnicodeDecodeError:
                return False
        return True
    return False


def inspect_upload(uploaded) -> Inspected:
    """Return what the file really is, or raise ValidationError with a message fit to show the user."""
    if uploaded is None:
        raise ValidationError("Choose a file to upload.")
    if uploaded.size == 0:
        raise ValidationError("That file is empty.")
    limit = max_bytes()
    if uploaded.size > limit:
        raise ValidationError(f"That file is too large (limit {human_size(limit)}).")
    clean = sanitize_filename(uploaded.name)
    ext = clean.rsplit(".", 1)[-1].lower() if "." in clean else ""
    kind = EXT_TO_KIND.get(ext)
    if kind is None:
        raise ValidationError("That file type isn't allowed. Allowed: " + ", ".join(ALLOWED_EXTENSIONS) + ".")
    if not _looks_like(kind, _head(uploaded), uploaded):
        raise ValidationError(f"That doesn't look like a real .{ext} file.")
    k = KINDS[kind]
    return Inspected(kind, ext, k.content_type, k.inline)


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_RESERVED = re.compile(r'[<>:"|?*]')                  # awkward or dangerous in file names on some systems
_BIDI = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\ufeff"))


def sanitize_filename(name: str, max_len: int = 150) -> str:
    """A display-safe version of the uploaded name. It is never used as a path on disk."""
    name = unicodedata.normalize("NFC", str(name or "")).translate(_BIDI)    # strip text-direction spoofing
    name = name.replace("\\", "/").split("/")[-1]                           # drop any directory part
    name = _RESERVED.sub("", _CONTROL.sub("", name)).strip().lstrip(".")
    name = re.sub(r"\s+", " ", name)
    if len(name) > max_len:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: max_len - len(ext) - 1] + "." + ext) if dot and len(ext) < 12 else name[:max_len]
    return name or "file"
