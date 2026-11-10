from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.views.decorators.http import require_POST
from django.views.generic import FormView
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import FormView, UpdateView

from .forms import SignupForm
from .models import Account, Membership, User
from core.crud import TenantViewMixin
from .forms import AccountSettingsForm, SignupForm


class SignupView(FormView):
    template_name = "accounts/signup.html"
    form_class = SignupForm

    def form_valid(self, form):
        with transaction.atomic():
            user = User.objects.create_user(form.cleaned_data["email"], form.cleaned_data["password1"])
            account = Account.objects.create(name=form.cleaned_data["account_name"],
                                             timezone=form.cleaned_data["timezone"])
            Membership.objects.create(user=user, account=account, role=Membership.Role.OWNER)
        login(self.request, user)
        self.request.session["account_id"] = account.pk
        return redirect("home")


class AccountSettingsView(TenantViewMixin, UpdateView):
    """Owner-only: rename the account and set its timezone."""

    form_class = AccountSettingsForm
    template_name = "accounts/settings.html"
    success_url = reverse_lazy("account_settings")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.membership and request.membership.role != Membership.Role.OWNER:
            raise PermissionDenied("Only owners can change account settings.")
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        return self.request.account                  # always the *active* account, never one named in the URL

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["local_now"] = timezone.localtime()      # rendered in the account's current timezone
        return ctx

    def form_valid(self, form):
        messages.success(self.request, "Account settings saved.")
        return super().form_valid(form)


@login_required
@require_POST
def switch_account(request):
    try:
        account_id = int(request.POST.get("account_id", ""))
    except ValueError:
        return HttpResponseForbidden("Invalid account.")
    # Only accounts the user actually belongs to may be activated.
    if not Membership.objects.filter(user=request.user, account_id=account_id).exists():
        return HttpResponseForbidden("Not a member of that account.")
    request.session["account_id"] = account_id
    return redirect("home")
