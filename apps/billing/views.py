from __future__ import annotations

import hashlib
import hmac
import json
import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth import update_session_auth_hash
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.workspaces.models import Workspace, WorkspaceMember
from .models import UserProfile


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_or_create_profile(user) -> UserProfile:
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


def _checkout_url(variant_id: str, user) -> str:
    """Build a LemonSqueezy checkout URL with pre-filled email and custom user_id."""
    base = f"https://store.lemonsqueezy.com/checkout/buy/{variant_id}"
    params = f"?checkout[email]={user.email}&checkout[custom][user_id]={user.id}"
    return base + params


# ── Checkout redirect ──────────────────────────────────────────────────────────

@login_required
def checkout_redirect(request: HttpRequest) -> HttpResponse:
    """Redirect to LemonSqueezy checkout for the Pro plan."""
    variant_id = os.environ.get("LEMONSQUEEZY_PRO_VARIANT_ID", "")
    if not variant_id:
        messages.error(request, "Payment configuration not set up yet. Please contact support.")
        return redirect("pricing")
    return redirect(_checkout_url(variant_id, request.user))


# ── LemonSqueezy Webhook ───────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["POST"])
def lemonsqueezy_webhook(request: HttpRequest) -> HttpResponse:
    """Handle LemonSqueezy subscription lifecycle events."""
    secret = os.environ.get("LEMONSQUEEZY_WEBHOOK_SECRET", "")
    if secret:
        signature = request.META.get("HTTP_X_SIGNATURE", "")
        digest = hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(digest, signature):
            return HttpResponse("Invalid signature", status=401)

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    event_name = payload.get("meta", {}).get("event_name", "")
    data = payload.get("data", {})
    attrs = data.get("attributes", {})
    meta_custom = payload.get("meta", {}).get("custom_data", {})

    user_id = meta_custom.get("user_id")
    if not user_id:
        return HttpResponse("ok")

    from django.contrib.auth.models import User
    try:
        user = User.objects.get(pk=int(user_id))
    except (User.DoesNotExist, (ValueError, TypeError)):
        return HttpResponse("ok")

    profile = _get_or_create_profile(user)

    if event_name in ("subscription_created", "subscription_updated", "subscription_resumed"):
        profile.ls_customer_id = str(attrs.get("customer_id", ""))
        profile.ls_subscription_id = str(data.get("id", ""))
        profile.ls_subscription_status = attrs.get("status", "active")
        profile.ls_variant_id = str(attrs.get("variant_id", ""))
        ends_at = attrs.get("ends_at") or attrs.get("renews_at")
        if ends_at:
            profile.subscription_ends_at = parse_datetime(ends_at)
        renews_at = attrs.get("renews_at")
        if renews_at:
            profile.subscription_renews_at = parse_datetime(renews_at)
        profile.plan = UserProfile.Plan.PRO
        profile.save()

    elif event_name in ("subscription_cancelled",):
        profile.ls_subscription_status = "cancelled"
        ends_at = attrs.get("ends_at")
        if ends_at:
            profile.subscription_ends_at = parse_datetime(ends_at)
        profile.save()

    elif event_name in ("subscription_expired",):
        profile.ls_subscription_status = "expired"
        profile.plan = UserProfile.Plan.FREE
        profile.save()

    elif event_name in ("subscription_paused",):
        profile.ls_subscription_status = "paused"
        profile.save()

    return HttpResponse("ok")


# ── Account settings ───────────────────────────────────────────────────────────

@login_required
def account_settings(request: HttpRequest) -> HttpResponse:
    profile = _get_or_create_profile(request.user)
    password_form = PasswordChangeForm(request.user)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "update_profile":
            user = request.user
            new_email = request.POST.get("email", "").strip()
            new_first = request.POST.get("first_name", "").strip()
            new_last = request.POST.get("last_name", "").strip()
            if new_email:
                user.email = new_email
            user.first_name = new_first
            user.last_name = new_last
            user.save(update_fields=["email", "first_name", "last_name"])
            messages.success(request, "Profile updated.")
            return redirect("account-settings")

        elif action == "change_password":
            password_form = PasswordChangeForm(request.user, request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, "Password changed successfully.")
                return redirect("account-settings")

        elif action == "cancel_subscription":
            if profile.ls_subscription_id:
                profile.ls_subscription_status = "cancelled"
                profile.subscription_ends_at = profile.subscription_renews_at or timezone.now()
                profile.save()
                messages.info(request, "Your subscription has been cancelled. You retain access until the end of the billing period.")
            return redirect("account-settings")

    return render(request, "billing/account_settings.html", {
        "profile": profile,
        "password_form": password_form,
    })


# ── Team / workspace settings ──────────────────────────────────────────────────

@login_required
def team_settings(request: HttpRequest) -> HttpResponse:
    profile = _get_or_create_profile(request.user)
    workspace = request.user.owned_workspaces.first()

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "create_workspace" and not workspace:
            name = request.POST.get("name", "").strip() or f"{request.user.username}'s Workspace"
            workspace = Workspace.objects.create(name=name, owner=request.user)
            WorkspaceMember.objects.get_or_create(
                workspace=workspace, user=request.user,
                defaults={"role": WorkspaceMember.Role.OWNER}
            )
            messages.success(request, "Workspace created.")
            return redirect("team-settings")

        elif action == "invite_member" and workspace:
            if not profile.is_pro:
                messages.error(request, "Team collaboration requires a Pro plan.")
                return redirect("team-settings")
            email = request.POST.get("email", "").strip()
            from django.contrib.auth.models import User as AuthUser
            try:
                invited = AuthUser.objects.get(email=email)
                _, created = WorkspaceMember.objects.get_or_create(
                    workspace=workspace, user=invited,
                    defaults={"role": WorkspaceMember.Role.MEMBER}
                )
                if created:
                    messages.success(request, f"Added {email} to your workspace.")
                else:
                    messages.info(request, f"{email} is already a member.")
            except AuthUser.DoesNotExist:
                messages.error(request, "No user found with that email address.")
            return redirect("team-settings")

        elif action == "remove_member" and workspace:
            member_id = request.POST.get("member_id")
            try:
                member = WorkspaceMember.objects.get(
                    pk=member_id, workspace=workspace
                )
                if member.role != WorkspaceMember.Role.OWNER:
                    member.delete()
                    messages.success(request, "Member removed.")
            except WorkspaceMember.DoesNotExist:
                pass
            return redirect("team-settings")

        elif action == "rename_workspace" and workspace:
            new_name = request.POST.get("name", "").strip()
            if new_name:
                workspace.name = new_name
                workspace.save(update_fields=["name"])
                messages.success(request, "Workspace renamed.")
            return redirect("team-settings")

    members = []
    if workspace:
        members = workspace.memberships.select_related("user").all()

    return render(request, "billing/team_settings.html", {
        "profile": profile,
        "workspace": workspace,
        "members": members,
    })
