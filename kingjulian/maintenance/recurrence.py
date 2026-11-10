"""Recurrence rules for maintenance tasks.

Rules are stored as a single RFC 5545 RRULE string (e.g. "FREQ=MONTHLY;INTERVAL=3").

Two scheduling modes:
  fixed             next date is computed from the task's original start date, so the cadence never
                    drifts, and a late completion skips missed dates instead of stacking overdue copies.
  after_completion  next date is computed from the day the work was actually done ("3 months after
                    the last service").

"Simple" rules (only FREQ and INTERVAL) are computed with relativedelta, which clamps month-ends
(Jan 31 + 1 month = Feb 28). dateutil's own rrule would silently *skip* months that lack the day
(Jan 31, Mar 31, May 31, ...), which is the wrong behaviour for maintenance. Complex rules
(BYDAY, BYMONTHDAY, COUNT, ...) use dateutil.
"""
from datetime import date, datetime, time

from dateutil.relativedelta import relativedelta
from dateutil.rrule import DAILY, MONTHLY, WEEKLY, YEARLY, rrulestr

FIXED = "fixed"
AFTER_COMPLETION = "after_completion"

FREQS = {"DAILY": DAILY, "WEEKLY": WEEKLY, "MONTHLY": MONTHLY, "YEARLY": YEARLY}
SUB_DAILY_KEYS = {"BYHOUR", "BYMINUTE", "BYSECOND"}

PRESETS = [
    ("", "Does not repeat"),
    ("FREQ=WEEKLY", "Weekly"),
    ("FREQ=MONTHLY", "Monthly"),
    ("FREQ=MONTHLY;INTERVAL=3", "Every 3 months"),
    ("FREQ=MONTHLY;INTERVAL=6", "Every 6 months"),
    ("FREQ=YEARLY", "Yearly"),
]
PRESET_LABELS = dict(PRESETS)

_EPOCH = datetime(2000, 1, 1)


def _dt(d: date) -> datetime:
    return datetime.combine(d, time.min)


def _parts(text: str) -> dict:
    try:
        return dict(p.split("=", 1) for p in text.split(";") if p)
    except ValueError:
        raise ValueError("Malformed rule: expected KEY=VALUE pairs separated by semicolons.")


def normalize_rule(rule: str) -> str:
    """Return the canonical rule text ('' for none) or raise ValueError with a user-facing message."""
    text = (rule or "").strip().upper()
    if text.startswith("RRULE:"):
        text = text[len("RRULE:"):]
    if not text:
        return ""
    if any(c in text for c in ":\n\r") or "DTSTART" in text:
        raise ValueError("Use a single rule such as FREQ=MONTHLY;INTERVAL=3 (no DTSTART or extra lines).")
    parts = _parts(text)
    if parts.get("FREQ") not in FREQS:
        raise ValueError("FREQ must be DAILY, WEEKLY, MONTHLY or YEARLY.")
    if SUB_DAILY_KEYS & set(parts):
        raise ValueError("Maintenance tasks repeat by day or longer; hourly rules are not supported.")
    try:
        if int(parts.get("INTERVAL", "1")) < 1:
            raise ValueError
        rr = rrulestr(text, dtstart=_EPOCH)
        later = rr.after(_EPOCH)
    except Exception:
        raise ValueError("That is not a valid recurrence rule.")
    if later is None:
        raise ValueError("That rule never produces a later date.")
    return text


def describe(rule: str, mode: str = FIXED) -> str:
    if not rule:
        return "One-off"
    label = PRESET_LABELS.get(rule, f"Custom ({rule})")
    return f"{label}, after completion" if mode == AFTER_COMPLETION else label


def _step(freq: str, n: int) -> dict:
    return {"DAILY": {"days": n}, "WEEKLY": {"weeks": n}, "MONTHLY": {"months": n}, "YEARLY": {"years": n}}[freq]


def _anchored_after(anchor: date, freq: str, interval: int, base: date) -> date:
    """First anchor + k*interval units that is strictly after `base`. Anchored, so no drift."""
    (unit,) = _step(freq, 1)
    longest_step_days = {"days": 1, "weeks": 7, "months": 31, "years": 366}[unit] * interval
    k = max(0, (base - anchor).days // longest_step_days - 1)   # safe lower bound; loop does the rest
    while True:
        candidate = anchor + relativedelta(**{unit: interval * k})
        if candidate > base:
            return candidate
        k += 1


def next_due(rule: str, *, mode: str, anchor: date, scheduled: date, completed_on: date) -> date | None:
    """Next due date after finishing an occurrence, or None if the rule is exhausted / not recurring.

    anchor        the task's original start date
    scheduled     the due date of the occurrence being closed
    completed_on  the day it was actually closed
    """
    rule = normalize_rule(rule)
    if not rule:
        return None
    parts = _parts(rule)
    freq, interval = parts["FREQ"], int(parts.get("INTERVAL", "1"))
    simple = set(parts) <= {"FREQ", "INTERVAL"}

    if mode == AFTER_COMPLETION:
        if simple:
            return completed_on + relativedelta(**_step(freq, interval))
        nxt = rrulestr(rule, dtstart=_dt(completed_on)).after(_dt(completed_on))
    else:
        base = max(scheduled, completed_on)         # skip dates already missed
        if simple:
            return _anchored_after(anchor, freq, interval, base)
        nxt = rrulestr(rule, dtstart=_dt(anchor)).after(_dt(base))
    return nxt.date() if nxt else None
