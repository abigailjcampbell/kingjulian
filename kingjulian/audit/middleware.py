from .context import _actor


class ActorMiddleware:
    """Record the signed-in user as the actor for anything saved during this request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user if request.user.is_authenticated else None
        token = _actor.set(user)
        try:
            return self.get_response(request)
        finally:
            _actor.reset(token)
