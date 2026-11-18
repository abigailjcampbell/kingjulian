# Permanent delete

Archive first, then delete. Permanent deletion is only offered for records that are **already archived**, from the Archive page
(`/archive/`), and only to **owners** (managers can restore but not destroy). It always goes through a confirmation page.

## The confirmation page (`/archive/<type>/<id>/delete/`)
Shows exactly what will be destroyed, including the names of the files, what will merely be changed, and what is in the way.
Deleting needs the word **DELETE** typed in (any case) plus an unchanged *plan*: the page carries a fingerprint of what would be deleted,
and if anything changed since it was shown (a file added, a new event linked), the deletion is refused and you are asked to review again.
A request that didn't come from the page (no matching fingerprint) can't delete anything. CSRF is enforced.

## What a deletion takes with it
| Type | Deleted along with it | Kept but changed | Blocks deletion while present |
|---|---|---|---|
| Contact | its files | tasks and events it was assigned to simply lose the contact; contact-property links removed | nothing |
| Maintenance task | **all its jobs (history and recorded costs)**, their reminders, its files and its jobs' files | nothing | nothing |
| Event | its files | nothing | nothing |
| Inventory item | its files | nothing | any maintenance task that uses it |
| Unit | nothing | nothing | any inventory item or task in it |
| Property | its files | contact-property links removed | any unit, item, task or event belonging to it |

Blocked deletions list what is in the way, with links to deal with each (delete or move them first). Spend history disappears from the dashboard
when a task with recorded costs is deleted; the confirmation page states the amount.

## Safety properties
* One database transaction: any failure rolls back everything. Files leave the disk only after the transaction commits.
* The database's own PROTECT rules remain underneath: code that forgets a child record fails instead of orphaning data.
* **One** activity entry per deletion (who, what, files, counts), not one per child row. The entry stays after the record is gone.
* The activity log is append-only, so earlier entries that mention the record's name remain. This is not a privacy "erase"; say so to anyone who asks.
* Missing or other-account records are reported neutrally ("no longer exists") and never touched; double submits are harmless.

## Limits
* No bulk "empty archive", and no scheduled auto-purge of old archived records.
* Backups still contain what you deleted until they age out.
