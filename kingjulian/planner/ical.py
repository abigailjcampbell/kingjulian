"""Minimal RFC 5545 writer for the calendar export (all-day items, plus floating-time events)."""
from datetime import datetime, timedelta

from .services import Entry

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


def _event_lines(e: Entry, account_id: int, stamp: datetime) -> list[str]:
    lines = ["BEGIN:VEVENT", f"UID:{e.uid}@a{account_id}.{UID_DOMAIN}", f"DTSTAMP:{stamp:%Y%m%dT%H%M%SZ}"]
    if e.start_time:
        begin = datetime.combine(e.start, e.start_time)
        finish = (datetime.combine(e.end, e.end_time) if e.end_time and (e.end > e.start or e.end_time > e.start_time)
                  else begin + timedelta(hours=1))
        lines += [f"DTSTART:{begin:%Y%m%dT%H%M%S}", f"DTEND:{finish:%Y%m%dT%H%M%S}"]   # floating local time
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


def build_ics(entries: list[Entry], *, account_name: str, account_id: int, now: datetime) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             f"X-WR-CALNAME:{escape(account_name)}"]
    for e in entries:
        lines += _event_lines(e, account_id, now)
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"
