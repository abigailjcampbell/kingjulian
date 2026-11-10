"""Request-scoped tenant context.

The active account id lives in a ContextVar. TenantManager reads it on every query, so a
forgotten `.filter(account=...)` can never leak another tenant's rows.
"""
import contextlib
import contextvars

_current_account_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "current_account_id", default=None
)


class TenantContextMissing(RuntimeError):
    """A tenant-scoped query ran with no active account."""


class TenantViolation(Exception):
    """An attempt to write across tenant boundaries."""


def get_current_account_id() -> int | None:
    return _current_account_id.get()


def require_current_account_id() -> int:
    account_id = _current_account_id.get()
    if account_id is None:
        raise TenantContextMissing(
            "No active account. Run inside core.tenancy.use_account(...), or use "
            "Model.all_tenants for deliberately cross-tenant access."
        )
    return account_id


def set_account(account_or_id):
    """Low-level: returns a token for reset_account(). Prefer use_account()."""
    return _current_account_id.set(getattr(account_or_id, "pk", account_or_id))


def reset_account(token) -> None:
    _current_account_id.reset(token)


@contextlib.contextmanager
def use_account(account_or_id):
    token = set_account(account_or_id)
    try:
        yield
    finally:
        reset_account(token)
