# Read-only record pages

Clicking an item now opens an information page; **Edit** is a button on it.

## Pages
`/properties/<id>/view/`, `/properties/units/<id>/view/`, `/inventory/<id>/view/`, `/contacts/<id>/view/`,
`/maintenance/tasks/<id>/view/`, `/planner/events/<id>/view/`. Edit pages keep their existing URLs (`/properties/<id>/` ...), so no existing
edit link, bookmark or test URL breaks.

Each page shows:
* the record's fields in readable form (choice labels, `$6,800.00`, `Mar 1, 2027`, `1:05 PM`), led by its Name/Title, with related records as links
  (a task links to its property, item and contact);
* **related sections**, only when non-empty and capped at 8 with an exact total and a filtered "See all N →" link:
  property: units, inventory, maintenance tasks, contacts, events; unit: inventory, tasks; item: tasks; contact: properties, assigned tasks, events;
  task: jobs (date, status, completion, cost) linking to each job's page. Summary rows: a task's next due, completed jobs and total recorded cost; an item's warranty status;
* the **Files** and **History** panels (they moved here from the edit form);
* buttons: **Edit**, **Archive** (with a confirmation) and **Back to the list**, for owners and managers.

## Who can open them
Every role can open them (viewers could never click through to an item before, since the only page was an edit form). Viewers get no buttons, no upload form
and no History panel. Edit pages remain owner/manager only. Archived records redirect owners/managers to the Archive page and are 404 for viewers.
Other accounts' records are 404.

## Where the app now links to the read-only page
List rows (all six lists), the dashboard, the calendar and agenda, the activity feed's jump links, file pages ("open record"), the Archive/delete flows, and the
Cancel button and post-save redirect of the edit form. Jobs (maintenance occurrences) keep their action page (complete / skip / reschedule); creating a record still returns to its list.

## Not changed
Job pages are not split into read-only + edit; they are action pages. Create forms are unchanged.
