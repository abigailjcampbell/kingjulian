import csv
import io
import re
import zipfile
from datetime import date, datetime, time
from decimal import Decimal

from django.http import HttpResponse
from django.utils import timezone
from django.utils.text import slugify

from audit.models import ActivityLog
from audit.signals import record
from core.tenancy import require_current_account_id

from .datasets import DATASETS

MAX_ROWS = 100_000
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")      # spreadsheet apps run these as formulas
BOM = "\ufeff"                                            # lets Excel read UTF-8 correctly


class TooManyRows(Exception):
    pass


# Digits, spaces, dots, commas, hyphens and parentheses only: no letters means no function names, cell references or
# DDE commands, so it cannot execute anything. This keeps phone numbers like "+1 801-555-0142" intact.
PLAIN_NUMBER = re.compile(r"[+-]?[0-9][0-9 ().,-]*")


def csv_safe(text: str) -> str:
    """Neutralise CSV/formula injection: text that starts like a formula gets a leading apostrophe."""
    if text.startswith(FORMULA_PREFIXES) and text != "-" and not PLAIN_NUMBER.fullmatch(text):    # a lone "-" is just a placeholder
        return "'" + text
    return text


def cell(value) -> str:
    """One CSV cell. Only *text* is sanitised; numbers keep their sign (-5.00 must stay -5.00)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, datetime):
        return timezone.localtime(value).isoformat(timespec="seconds") if timezone.is_aware(value) else value.isoformat(timespec="seconds")
    if isinstance(value, (date, time)):
        return value.isoformat(timespec="minutes") if isinstance(value, time) else value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (int, float)):
        return str(value)
    return csv_safe(str(value))


def render_csv(dataset, queryset) -> tuple[str, int]:
    rows = list(queryset[: MAX_ROWS + 1])
    if len(rows) > MAX_ROWS:
        raise TooManyRows(f"{dataset.title}: more than {MAX_ROWS:,} rows. Narrow it down with filters and export again.")
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow([header for header, _ in dataset.columns])
    for obj in rows:
        writer.writerow([cell(getter(obj)) for _, getter in dataset.columns])
    return BOM + buf.getvalue(), len(rows)


def _filename(dataset_key, account, ext):
    return f"{dataset_key}-{slugify(account.name) or 'account'}-{timezone.localdate():%Y-%m-%d}.{ext}"


def _log_export(summary, label):
    record(account_id=require_current_account_id(), action=ActivityLog.Action.EXPORTED, summary=summary,
           target=("export", None, label))


def csv_response(key, base_queryset, account):
    """CSV download. `base_queryset` is what the user is looking at (filters applied) or None for everything."""
    dataset = DATASETS[key]
    try:
        text, n = render_csv(dataset, dataset.queryset(base_queryset))
    except TooManyRows as exc:
        return HttpResponse(str(exc), status=413, content_type="text/plain")
    _log_export(f"Exported {dataset.title.lower()} ({n} row{'s' if n != 1 else ''}) as CSV", dataset.title)
    response = HttpResponse(text, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{_filename(key, account, "csv")}"'
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def zip_response(account):
    """Everything (archived rows included) as one zip of CSVs plus a README."""
    out, counts = io.BytesIO(), {}
    try:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for key, dataset in DATASETS.items():
                text, n = render_csv(dataset, dataset.queryset())
                counts[dataset.title] = n
                z.writestr(f"{key}.csv", text)
            stamp = timezone.localtime().strftime("%Y-%m-%d %H:%M %Z")
            z.writestr("README.txt", f"Export of {account.name} ({account.timezone})\nGenerated {stamp}\n\n"
                       + "\n".join(f"{name}: {n} rows" for name, n in counts.items())
                       + "\n\nArchived records are included (see the Archived column). Files themselves are not included.\n")
    except TooManyRows as exc:
        return HttpResponse(str(exc), status=413, content_type="text/plain")
    _log_export(f"Exported all data ({sum(counts.values())} rows in {len(counts)} files) as a zip", "All data")
    response = HttpResponse(out.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{_filename("all-data", account, "zip")}"'
    response["Cache-Control"] = "private, no-store"
    return response
