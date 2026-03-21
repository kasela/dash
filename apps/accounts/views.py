from django.contrib.auth import login
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .forms import AppLoginForm, AppSignupForm

SIGNUP_BENEFITS = [
    "Upload CSV, Excel, or JSON files in seconds",
    "AI-powered data profiling and chart suggestions",
    "One-click shareable dashboard links",
    "Free forever on the Starter plan",
    "No credit card required",
]


class AppLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = AppLoginForm
    redirect_authenticated_user = True


class AppLogoutView(LogoutView):
    next_page = "landing"


@require_http_methods(["GET", "POST"])
def signup_view(request):
    if request.user.is_authenticated:
        return redirect("app-home")

    form = AppSignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        return redirect("app-home")

    return render(request, "accounts/signup.html", {"form": form, "signup_benefits": SIGNUP_BENEFITS})
