# maintenance: step 3

Recurring maintenance tasks, a to-do list of due dates, and a daily reminder job.

## Model: template vs. occurrence
| Model | Role |
|---|---|
| `MaintenanceTask` | The template: what, where (property/unit/item), who (contact), start date, RRULE, schedule mode, reminder offsets |
| `TaskOccurrence` | One due date. Holds status (open/done/skipped), completion date, cost, notes: the history |
| `Reminder` | One planned notification. Unique on (occurrence, kind, remind_on), which makes the job idempotent |

A task has **at most one open occurrence**, enforced by a partial unique index. Completing or skipping goes through
`services.complete_occurrence` / `skip_occurrence`, which lock the row, close it, and open the next one atomically.

## Scheduling modes
* **fixed**: dates come from the original start date (Jan 31 monthly gives Feb 28, Mar 31, Apr 30; no drift). Finishing late skips missed dates instead of stacking overdue copies.
* **after_completion**: next date = completion day + interval ("3 months after the last service").

Simple rules (FREQ + INTERVAL) use relativedelta so month-ends clamp. dateutil's rrule would silently skip months lacking the day. Rules with BYDAY/BYMONTHDAY/COUNT use dateutil.

## Daily job
```bash
python manage.py send_maintenance_reminders            # cron, once a day, e.g.:  0 7 * * *
python manage.py send_maintenance_reminders --dry-run  # report only; rolls everything back
python manage.py send_maintenance_reminders --today 2026-12-01   # replay a date
```
Safe to run twice or after downtime: if several reminder slots are live at once only the newest is sent. Emails go (BCC) to
owners and managers of the account that owns the task; viewers and other accounts never receive them. Failed sends retry
on later runs, up to 3 attempts. Set `EMAIL_BACKEND`, `DEFAULT_FROM_EMAIL`, `SITE_URL` for production.

## Demo data
`python manage.py seed_demo && python manage.py seed_maintenance`

## Known limits / decisions to revisit
* An overdue task gets **one** overdue email, not a recurring nag.
* Each account has its own timezone (Settings); due dates, overdue and reminders use the account's local date.
* Editing a task's schedule applies from the *next* occurrence; use the occurrence page to move the current due date.
* Assigned contacts (vendors, tenants) are never emailed automatically.
* Delivery is at-least-once: a crash between SMTP send and commit can re-send one reminder.
* `Reminder` points at its occurrence with a real FK (simpler and safer than the generic target_type/target_id sketched earlier).
