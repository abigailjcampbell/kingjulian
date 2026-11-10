"""Minimal RFC 5545 writer for the calendar export (all-day items, plus floating-time events).
   Timed events are converted from the account's local time to UTC."""
from datetime import datetime, timedelta, timezone as dt_timezone

from .services import Entry

UTC = dt_timezone.utc
PRODID = "-//Property Manager//Planner//EN"
UID_DOMAIN = "propmgr.local"


def escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n"))


def fold(line: str) -> str:
    """Wrap at 75 octets (RFC 5545 3.1) without ever splitting a multi-byte character."""
    if len(line.encode()) <= 75:
        return line
    parts, current, limit = [], b"", 75
    for ch in line:
        raw = ch.encode()
        if len(current) + len(raw) > limit:
            parts.append(current.decode())
            current, limit = b"", 74           # continuation lines start with one space
        current += raw
    parts.append(current.decode())
    return "\r\n ".join(parts)


def _utc(day, clock, zone) -> datetime:
    """Local wall-clock time in `zone` -> UTC. Nonexistent (spring-forward) and ambiguous (fall-back)
    times resolve the way zoneinfo does (fold=0), so they never raise."""
    return datetime.combine(day, clock, tzinfo=zone).astimezone(UTC)


def _event_lines(e: Entry, account_id: int, stamp: datetime, zone) -> list[str]:
    lines = ["BEGIN:VEVENT", f"UID:{e.uid}@a{account_id}.{UID_DOMAIN}", f"DTSTAMP:{stamp:%Y%m%dT%H%M%SZ}"]
    if e.start_time:
        begin = _utc(e.start, e.start_time, zone)
        has_end = e.end_time and (e.end > e.start or e.end_time > e.start_time)
        finish = _utc(e.end, e.end_time, zone) if has_end else begin + timedelta(hours=1)
        if finish <= begin:          # e.g. 02:30-03:30 across a spring-forward gap collapses to zero length
            finish = begin + timedelta(hours=1)
        lines += [f"DTSTART:{begin:%Y%m%dT%H%M%SZ}", f"DTEND:{finish:%Y%m%dT%H%M%SZ}"]
    else:
        lines += [f"DTSTART;VALUE=DATE:{e.start:%Y%m%d}", f"DTEND;VALUE=DATE:{e.end + timedelta(days=1):%Y%m%d}"]
        lines.append("TRANSP:TRANSPARENT")                                               # don't block free/busy
    title = e.title + (" (done)" if e.status == "done" else " (overdue)" if e.status == "overdue" else "")
    lines.append(f"SUMMARY:{escape(title)}")
    description = "\n".join(filter(None, [e.where, e.detail]))
    if description:
        lines.append(f"DESCRIPTION:{escape(description)}")
    if e.where:
        lines.append(f"LOCATION:{escape(e.where)}")
    lines += [f"CATEGORIES:{escape(e.label)}", "END:VEVENT"]
    return lines


def build_ics(entries: list[Entry], *, account_name: str, account_id: int, now: datetime, zone=UTC) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             f"X-WR-CALNAME:{escape(account_name)}", f"X-WR-TIMEZONE:{getattr(zone, 'key', 'UTC')}"]
    for e in entries:
        lines += _event_lines(e, account_id, now, zone)
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"
