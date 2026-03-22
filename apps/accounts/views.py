from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from .forms import AppLoginForm, AppSignupForm
from .models import ApiKey

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


@login_required
@require_POST
def api_key_create(request):
    name = request.POST.get("name", "").strip() or "My API Key"
    raw_key = ApiKey.generate_key()
    ApiKey.objects.create(user=request.user, key=raw_key, name=name)
    return JsonResponse({"key": raw_key, "name": name})


@login_required
@require_POST
def api_key_revoke(request, key_id):
    key = get_object_or_404(ApiKey, pk=key_id, user=request.user)
    key.delete()
    return JsonResponse({"status": "revoked"})
