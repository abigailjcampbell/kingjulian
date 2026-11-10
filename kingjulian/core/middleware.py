from .tenancy import reset_account, set_account


class ActiveAccountMiddleware:
    """Resolve the user's active account from the session and open the tenant context.

    The session value is only a hint: it is honoured only if the user has a Membership in
    that account, otherwise we fall back to their first membership.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from accounts.models import Membership  # avoid import at settings load

        request.account = None
        request.membership = None
        request.memberships = []
        token = None

        if request.user.is_authenticated:
            memberships = list(
                Membership.objects.select_related("account").filter(user=request.user).order_by("id")
            )
            request.memberships = memberships
            wanted = request.session.get("account_id")
            chosen = next((m for m in memberships if m.account_id == wanted), None)
            if chosen is None and memberships:
                chosen = memberships[0]
            if chosen is not None:
                request.membership = chosen
                request.account = chosen.account
                request.session["account_id"] = chosen.account_id
                token = set_account(chosen.account_id)
        try:
            return self.get_response(request)
        finally:
            if token is not None:
                reset_account(token)
