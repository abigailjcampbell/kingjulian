from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.views.decorators.http import require_POST
from django.views.generic import FormView

from .forms import SignupForm
from .models import Account, Membership, User


class SignupView(FormView):
    template_name = "accounts/signup.html"
    form_class = SignupForm

    def form_valid(self, form):
        with transaction.atomic():
            user = User.objects.create_user(form.cleaned_data["email"], form.cleaned_data["password1"])
            account = Account.objects.create(name=form.cleaned_data["account_name"])
            Membership.objects.create(user=user, account=account, role=Membership.Role.OWNER)
        login(self.request, user)
        self.request.session["account_id"] = account.pk
        return redirect("home")


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
