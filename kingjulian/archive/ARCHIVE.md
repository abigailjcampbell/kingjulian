# Archive: view and restore archived records

Archiving a property, unit, inventory item, contact, maintenance task, or event hides it everywhere but deletes nothing.
The **Archive** page (menu, owners and managers only) lists archived records of every type, shows one read-only, and restores it.

## Pages
* `/archive/`: all archived records, newest first, with type, where it lives, when and by whom it was archived (from the activity log).
  Filter by type (counts shown), search by name/serial/company/property, 25 per page.
* `/archive/<type>/<id>/`: read-only view: every field in readable form (choice labels, related names, "Repeats: Yearly"), its files
  (downloadable, no uploads while archived) and its history, plus the **Restore** button.
* `POST /archive/<type>/<id>/restore/`: restores it. Recorded in the activity log as "Restored ..." with the person who did it.
* After archiving anything, the confirmation reads "Archived X. **Undo**", linking straight to its archive page.

## Restore rules
* **Parents first.** A record can't come back while something it points at is still archived (a unit or item needs its property; a task
  needs its property and any item/unit/contact it names; an event its property/contact). The page explains what to restore first and links to it.
  This stops a visible record from pointing at something hidden, and stops edit forms from silently dropping the link.
* **Name clashes.** A unit's name is freed when it is archived, so a new unit may take it. Restoring the old one then is refused with an explanation, and nothing changes.
* Restoring is idempotent (a second click says "already active"). Restored records are exactly as they were; contact-property links and files are untouched.
* Archiving a property does **not** archive its units, items or tasks, and restoring doesn't touch them either.

## Not included
Permanent delete, bulk restore, and an "include archived" switch on the normal list pages. Archived rows are also in the Export page's CSVs (with the date in the *Archived* column).
