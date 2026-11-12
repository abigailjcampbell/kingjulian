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
