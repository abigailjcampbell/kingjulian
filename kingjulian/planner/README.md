# planner: step 4

Calendar, agenda, `.ics` export, manual events, and the dashboard that is now the home page.

## The calendar is a query, not a table
Only manual entries are stored (`Event`: inspection, showing, lease milestone, other). Everything else is **computed on read** from the table that owns it, so nothing can go stale or be double-entered:

| Source | Shown on | Notes |
|---|---|---|
| `Event` | its date(s); multi-day events appear on every day | optional start/end time, up to 366 days |
| `TaskOccurrence` (open + done) | its due date | overdue = open and before today; skipped and archived tasks are hidden |
| `InventoryItem.warranty_expires_on` | the expiry date | "expiring" vs "expired" relative to today |

`planner.services.calendar_entries(start, end, today=, can_write=)` runs **three queries** however much data exists. All-day items sort first, then timed items by clock. Links are only emitted for pages the user's role can open (viewers get event/warranty chips without links).

## Pages
* `/` dashboard: overdue, due in 7 / 30 days, upcoming events (14 days, including ones already underway), warranties ending (next 60 days) and recently expired (last 30), items in poor condition or needing replacement, maintenance spend over the last 365 days (completed work with a recorded cost, total and top 5 properties).
* `/planner/?month=YYYY-MM` month grid, Sunday-first, previous/next/today, `+N more` collapses crowded days into the agenda. Bad `month` values fall back to the current month.
* `/planner/agenda/?from=YYYY-MM-DD&days=7|14|30|60|90` grouped list with an overdue section when the window reaches today.
* `/planner/events/` event list (upcoming by default), plus create/edit/archive; `?date=` pre-fills the date from a calendar day.
* `/planner/calendar.ics` download of the window from 31 days back to 366 days ahead.

## .ics notes
Written by hand to RFC 5545 (escaping, 75-octet folding that never splits a character, CRLF) and validated in tests with the `icalendar` parser. All-day items use exclusive `DTEND`. Timed events are **floating local times** (no timezone). Each item has a stable UID scoped to the account, so re-importing updates rather than duplicates. This is a **download**, not a live subscription: calendar apps can't log in, so subscribing needs per-user secret URLs (not built).

## Known limits / decisions to revisit
* No account timezone yet: "today" and event times use the server's date/clock (same gap as the reminder job). One `Account.timezone` would fix both.
* Events don't repeat (an annual insurance review is entered each year). The recurrence engine from step 3 could be reused.
* A completed task shows on its **due** date, not the day it was done.
* Spend only counts completed work that has a cost entered.

## Demo data
`python manage.py seed_demo && python manage.py seed_maintenance && python manage.py seed_planner`
