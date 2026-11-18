"""Who is performing the current action. Set per request by ActorMiddleware; None means 'the system'
(management commands, background jobs)."""
import contextlib
import contextvars

_actor = contextvars.ContextVar("audit_actor", default=None)


def get_actor():
    return _actor.get()


@contextlib.contextmanager
def acting_as(user):
    token = _actor.set(user)
    try:
        yield
    finally:
        _actor.reset(token)

_quiet = contextvars.ContextVar("audit_quiet", default=False)


def is_quiet() -> bool:
    return _quiet.get()


@contextlib.contextmanager
def quiet():
    """Suppress the automatic per-row entries while a bulk operation runs; the caller then records one summary entry."""
    token = _quiet.set(True)
    try:
        yield
    finally:
        _quiet.reset(token)