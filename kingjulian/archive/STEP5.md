# Step 5: attachments, activity log, CSV export

Three new apps: `attachments` (files on records), `audit` (who changed what), `exports` (CSV and zip downloads).

## Attachments
* A file belongs to exactly **one** property, inventory item, task, maintenance job, contact, or event. Each is a real foreign key (so tenant isolation and integrity are enforced by the database and `TenantModel`), with a CHECK constraint for "exactly one target".
* **Validation is by content, not by what the browser says.** Allow-list: jpg/jpeg, png, gif, webp, heic/heif, pdf, docx, xlsx, txt, csv. Each type's bytes are sniffed (magic numbers; DOCX/XLSX must contain their real parts, read from the zip directory only, never expanded). HTML, SVG, scripts, archives and executables are never accepted. Filenames are sanitised for display (path parts, control and text-direction characters, `<>:"|?*` removed) and **never used on disk**.
* **Storage:** `MEDIA_ROOT/attachments/<account id>/<random 32 hex>.<ext>`, mode 0640 in 0750 folders, outside any served path. Files are served only by authenticated, tenant-scoped views: `/files/<id>/download/` (always `attachment`) and `/files/<id>/view/` (images only), both with `nosniff`, `Cache-Control: private, no-store` and `Content-Security-Policy: sandbox`.
* **Limits** (settings): 10 MB per file, 500 MB per account, 50 files per record. An `UploadSizeMiddleware` rejects oversized uploads from `Content-Length` before the body is read.
* **Deletes** remove the row immediately and the file after the transaction commits (a rollback can't lose a file). `python manage.py purge_orphan_attachments [--dry-run]` removes unreferenced files older than 24 hours (left by a crash); run it weekly.
* Owners/managers upload and delete; every role can browse and download. Pages: `/files/` (search, filter), `/files/<id>/`, and a **Files** panel on each record's edit page.

## Activity log
* Append-only `ActivityLog` rows written by signals in the **same transaction** as the change (a rollback leaves no trace; a failure to write the log fails the save). Covers properties, units, contacts, items, tasks, events, job completions/skips/reopens/reschedules, file uploads and deletions, contact-property links, and exports.
* Each entry stores the actor (email snapshot), a readable summary, and a before/after diff using people-friendly values (choice labels, names for foreign keys, ISO dates, `640.00`). No-op saves log nothing; partial saves (`update_fields`) report only what was written. Scheduler-created occurrences are not logged; actions outside a web request show as "System".
* `/activity/` (owners and managers): search, filter by action/type/person/record, jump to the record. A **History** panel shows each record's latest entries.

## CSV export
* **Export CSV** on every list exports exactly what you are filtering (all pages), owners and managers only. `/exports/` has nine datasets (including archived rows) and **all data as a zip** with a README. Every export is logged.
* UTF-8 with BOM (Excel reads accents correctly), CRLF, ISO dates, account-timezone timestamps. **Formula injection is neutralised**: text starting with `= + - @ tab CR` gets a leading apostrophe; numbers keep their sign; plain phone-like strings (`+1 801-555-0142`) and a lone `-` are left alone. Verified by opening real exports in LibreOffice: protected cells open as text; an unprotected twin turned `=HYPERLINK(...)` into a live formula.
* Exports are built in memory and capped at 100,000 rows per dataset (refused with a 413, never silently truncated).

## Measured (5,000 inventory rows)
CSV export 340 ms in 8 queries; zip 285 ms; first inventory page 44 ms in 9 queries; audit overhead about 1.5 ms per save; activity feed page with 3,300 entries 18 ms.

## Deployment notes
* Put an upload size limit in the reverse proxy too (nginx `client_max_body_size 12m;`).
* Back up `MEDIA_ROOT` together with the database; exports do not include file contents.
* Set `MEDIA_ROOT` in the environment to a persistent volume.

## Known limits
* `QuerySet.update()`, `bulk_create()` and queryset `.delete()` bypass signals, so they bypass the audit log (the same gap as the tenant guard); "append-only" is enforced in code, not by database triggers.
* No virus scanning; photo metadata (including GPS location) is not stripped; one file per upload.
* Quota and per-record limits are checked per upload, so two simultaneous uploads can overshoot slightly.
* The log is kept forever (no retention job yet) and records text-field changes, so it can contain sensitive notes; it is visible only to owners and managers of the same account.
