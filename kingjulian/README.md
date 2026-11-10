# Property Manager: steps 1 & 2 scaffold

Django 5.2 + Postgres. Multi-tenant from day one; HTMX search/filter on every list.

## Run it
```bash
pip install -r requirements.txt
export DATABASE_URL=postgres://user:pass@localhost:5432/propmgr   # default: propmgr/propmgr@localhost
python manage.py migrate
python manage.py seed_demo          # two demo tenants; password demo-pass-12345
python manage.py runserver
python manage.py test               # 40 tests; needs a Postgres role with CREATEDB
```
Demo logins: `owner@sunrise.test`, `viewer@sunrise.test`, `owner@lakeside.test`, `both@demo.test` (member of both accounts, shows the switcher).

## How tenancy works
| Piece | Where | What it does |
|---|---|---|
| `use_account()` / ContextVar | `core/tenancy.py` | Holds the active account id for the current request/job |
| `ActiveAccountMiddleware` | `core/middleware.py` | Picks the account from the session **only if the user has a Membership in it** |
| `TenantManager` (`objects`) | `core/models.py` | Filters every query by account. The filter resolves at SQL-compile time, so executing a query with no active account raises `TenantContextMissing` (fails closed) |
| `all_tenants` | `core/models.py` | Deliberately unscoped manager: admin, commands, tests only |
| `TenantModel.save()` | `core/models.py` | Sets `account` from context, refuses writes into a different account, and refuses any FK pointing at another account's row |
| Role check | `core/crud.py` | owner/manager can write; viewer is read-only |

Rules: build ModelForms with an explicit `fields` list (a test enforces `account` is never exposed); use `all_tenants` only on purpose.

## Known limits (deliberate, for later)
- `bulk_create` and `QuerySet.update()` bypass `save()`, so they skip the account-set and FK checks. `update()` is still scoped by the manager; `bulk_create` needs `account_id` set explicitly.
- The cross-tenant FK check is application-level. For defense in depth, add Postgres row-level security or composite `(id, account_id)` foreign keys.
- Archiving a property does not cascade to its units/items.
- No email verification, invites, or password reset yet.
