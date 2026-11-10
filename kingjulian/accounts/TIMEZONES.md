# Account timezones

Every account has an IANA timezone (`Account.timezone`, default `UTC`). It decides what **"today"** means.

## What it controls
| Thing | Behaviour |
|---|---|
| Overdue / "Due today" / "In N days" | judged against the account's local date |
| Dashboard windows, calendar "today" cell, agenda start, `.ics` window | anchored to the account's local date |
| "Completed on" default and the no-future-dates check | local date (an Auckland owner can log this morning's work; an LA owner can't claim tomorrow) |
| Reminder job | each account gets its own "today"; one run can send Auckland's "due today" while Los Angeles is still on the previous day |
| Event times | read as local time in the account's zone; the `.ics` export converts them to UTC (`...Z`), so calendar apps place them correctly |
| All-day items, due dates, warranty dates | plain calendar dates: unaffected by the zone |

## How it works
* `core.middleware.ActiveAccountMiddleware` calls `timezone.activate(account.zone)` for the request and `deactivate()` afterwards, so everything that calls `timezone.localdate()` / `localtime()` is automatically correct. No view had to change.
* `Account.today()` is the same thing outside a request (the reminder job uses it per account).
* An unreadable stored value falls back to UTC instead of failing (`accounts.timezones.load_zone` never raises).
* Owners change it at `/accounts/settings/` (also: signup preselects the browser's zone). The picker lists canonical names only (no `US/Eastern`, `Etc/GMT+5`...). A legacy value already stored stays selectable.

## Running the job
`python manage.py send_maintenance_reminders` can run **hourly or daily**; it is idempotent. Each account is reminded once its own local date reaches the reminder day. `--today YYYY-MM-DD` forces one date for every account (testing).

## Things to know
* Changing an account's zone re-reads **event times** in the new zone (a 10:00 event now means 10:00 there). Dates don't move.
* Existing accounts were migrated to `UTC`; owners should pick their zone in Settings.
* Daylight-saving edge cases are handled the way `zoneinfo` does (a nonexistent 02:30 resolves forward, an ambiguous 01:30 takes the first occurrence); an exported event is never zero-length.
* Needs the `tzdata` package on platforms without a system timezone database.
