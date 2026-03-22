import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import user_passes_test
from django.db.models import Count, Q
from django.db.models.functions import TruncMonth
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.contrib import messages

from apps.billing.models import UserProfile
from apps.dashboards.models import Dashboard, DashboardWidget
from apps.datasets.models import Dataset, DatasetVersion
from apps.workspaces.models import Workspace

User = get_user_model()

staff_required = user_passes_test(lambda u: u.is_active and u.is_staff, login_url="/accounts/login/")


# ── Plan limits config (single source of truth) ────────────────────────────────

PLAN_LIMITS = {
    "free": {
        "name": "Free",
        "color": "slate",
        "price_monthly": 0,
        "max_dashboards": 3,
        "max_monthly_uploads": 5,
        "ai_features": False,
        "team_collaboration": False,
        "api_access": False,
        "public_sharing": True,
        "priority_support": False,
    },
    "pro": {
        "name": "Pro",
        "color": "indigo",
        "price_monthly": 29,
        "max_dashboards": 999_999,
        "max_monthly_uploads": 999_999,
        "ai_features": True,
        "team_collaboration": True,
        "api_access": True,
        "public_sharing": True,
        "priority_support": True,
    },
    "enterprise": {
        "name": "Enterprise",
        "color": "violet",
        "price_monthly": 99,
        "max_dashboards": 999_999,
        "max_monthly_uploads": 999_999,
        "ai_features": True,
        "team_collaboration": True,
        "api_access": True,
        "public_sharing": True,
        "priority_support": True,
    },
}


def _months_ago_range(n: int):
    """Return list of (year, month) tuples for the last n months including current."""
    now = timezone.now()
    result = []
    for i in range(n - 1, -1, -1):
        dt = now - timedelta(days=30 * i)
        result.append((dt.year, dt.month))
    return result


# ── Overview ───────────────────────────────────────────────────────────────────

@staff_required
def admin_overview(request):
    now = timezone.now()
    thirty_days_ago = now - timedelta(days=30)
    seven_days_ago = now - timedelta(days=7)

    total_users = User.objects.count()
    new_users_30d = User.objects.filter(date_joined__gte=thirty_days_ago).count()
    new_users_7d = User.objects.filter(date_joined__gte=seven_days_ago).count()

    plan_counts = UserProfile.objects.values("plan").annotate(count=Count("id"))
    plan_map = {row["plan"]: row["count"] for row in plan_counts}
    free_count = plan_map.get("free", 0)
    pro_count = plan_map.get("pro", 0)
    enterprise_count = plan_map.get("enterprise", 0)
    paid_count = pro_count + enterprise_count

    active_subs = UserProfile.objects.filter(ls_subscription_status="active").count()
    cancelled_subs = UserProfile.objects.filter(ls_subscription_status="cancelled").count()
    paused_subs = UserProfile.objects.filter(ls_subscription_status="paused").count()
    past_due_subs = UserProfile.objects.filter(ls_subscription_status="past_due").count()

    total_dashboards = Dashboard.objects.count()
    dashboards_30d = Dashboard.objects.filter(created_at__gte=thirty_days_ago).count()
    total_datasets = DatasetVersion.objects.count()
    total_workspaces = Workspace.objects.count()

    # Chart: users registered by month (last 12 months)
    months = _months_ago_range(12)
    start_dt = timezone.now().replace(month=months[0][1], day=1, hour=0, minute=0, second=0, microsecond=0)
    # Use year from months[0]
    import calendar
    from datetime import datetime
    start_dt = timezone.make_aware(datetime(months[0][0], months[0][1], 1))

    users_by_month_qs = (
        User.objects
        .filter(date_joined__gte=start_dt)
        .annotate(month=TruncMonth("date_joined"))
        .values("month")
        .annotate(count=Count("id"))
        .order_by("month")
    )
    users_by_month_map = {
        (row["month"].year, row["month"].month): row["count"]
        for row in users_by_month_qs
    }
    month_labels = []
    month_user_data = []
    for y, m in months:
        month_labels.append(f"{calendar.month_abbr[m]} {y}")
        month_user_data.append(users_by_month_map.get((y, m), 0))

    # Chart: dashboards created by month (last 12 months)
    dashboards_by_month_qs = (
        Dashboard.objects
        .filter(created_at__gte=start_dt)
        .annotate(month=TruncMonth("created_at"))
        .values("month")
        .annotate(count=Count("id"))
        .order_by("month")
    )
    dash_by_month_map = {
        (row["month"].year, row["month"].month): row["count"]
        for row in dashboards_by_month_qs
    }
    month_dash_data = [dash_by_month_map.get((y, m), 0) for y, m in months]

    # Recent users (last 10)
    recent_users = User.objects.select_related("profile").order_by("-date_joined")[:10]

    context = {
        "total_users": total_users,
        "new_users_30d": new_users_30d,
        "new_users_7d": new_users_7d,
        "free_count": free_count,
        "pro_count": pro_count,
        "enterprise_count": enterprise_count,
        "paid_count": paid_count,
        "active_subs": active_subs,
        "cancelled_subs": cancelled_subs,
        "paused_subs": paused_subs,
        "past_due_subs": past_due_subs,
        "total_dashboards": total_dashboards,
        "dashboards_30d": dashboards_30d,
        "total_datasets": total_datasets,
        "total_workspaces": total_workspaces,
        "month_labels_json": json.dumps(month_labels),
        "month_user_data_json": json.dumps(month_user_data),
        "month_dash_data_json": json.dumps(month_dash_data),
        "plan_labels_json": json.dumps(["Free", "Pro", "Enterprise"]),
        "plan_data_json": json.dumps([free_count, pro_count, enterprise_count]),
        "sub_status_json": json.dumps({
            "labels": ["Active", "Cancelled", "Paused", "Past Due"],
            "data": [active_subs, cancelled_subs, paused_subs, past_due_subs],
        }),
        "recent_users": recent_users,
    }
    return render(request, "admin_portal/overview.html", context)


# ── Users ──────────────────────────────────────────────────────────────────────

@staff_required
def admin_users(request):
    q = request.GET.get("q", "").strip()
    plan_filter = request.GET.get("plan", "")
    status_filter = request.GET.get("status", "")

    qs = User.objects.select_related("profile").order_by("-date_joined")

    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(email__icontains=q))

    if plan_filter:
        qs = qs.filter(profile__plan=plan_filter)

    if status_filter == "staff":
        qs = qs.filter(is_staff=True)
    elif status_filter == "active":
        qs = qs.filter(is_active=True)
    elif status_filter == "inactive":
        qs = qs.filter(is_active=False)

    # Annotate with dashboard / dataset counts
    qs = qs.annotate(
        dashboard_count=Count("owned_workspaces__dashboards", distinct=True),
    )

    context = {
        "users": qs,
        "q": q,
        "plan_filter": plan_filter,
        "status_filter": status_filter,
        "total_count": qs.count(),
        "plan_choices": [("", "All Plans"), ("free", "Free"), ("pro", "Pro"), ("enterprise", "Enterprise")],
        "status_choices": [("", "All"), ("active", "Active"), ("inactive", "Inactive"), ("staff", "Staff")],
    }
    return render(request, "admin_portal/users.html", context)


@staff_required
def admin_user_detail(request, user_id):
    target_user = get_object_or_404(User.objects.select_related("profile"), pk=user_id)

    # Ensure profile exists
    profile, _ = UserProfile.objects.get_or_create(user=target_user)

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "change_plan":
            new_plan = request.POST.get("plan", "")
            if new_plan in ("free", "pro", "enterprise"):
                old_plan = profile.plan
                profile.plan = new_plan
                if new_plan == "free":
                    # Clear billing fields when downgrading to free
                    profile.ls_subscription_id = ""
                    profile.ls_subscription_status = ""
                    profile.subscription_ends_at = None
                    profile.subscription_renews_at = None
                profile.save()
                messages.success(request, f"Plan changed from {old_plan} → {new_plan}.")
            else:
                messages.error(request, "Invalid plan selected.")

        elif action == "toggle_active":
            target_user.is_active = not target_user.is_active
            target_user.save()
            state = "activated" if target_user.is_active else "deactivated"
            messages.success(request, f"User {state}.")

        elif action == "toggle_staff":
            target_user.is_staff = not target_user.is_staff
            target_user.save()
            state = "granted" if target_user.is_staff else "revoked"
            messages.success(request, f"Staff access {state}.")

        elif action == "update_subscription":
            profile.ls_subscription_status = request.POST.get("ls_subscription_status", profile.ls_subscription_status)
            ends_at = request.POST.get("subscription_ends_at", "")
            if ends_at:
                from datetime import datetime
                try:
                    profile.subscription_ends_at = timezone.make_aware(datetime.fromisoformat(ends_at))
                except ValueError:
                    pass
            profile.save()
            messages.success(request, "Subscription updated.")

        return redirect("admin-user-detail", user_id=user_id)

    # Get user's workspaces and dashboards
    workspaces = Workspace.objects.filter(
        Q(owner=target_user) | Q(memberships__user=target_user)
    ).distinct().prefetch_related("dashboards")

    dashboards = Dashboard.objects.filter(
        workspace__in=workspaces
    ).select_related("workspace").order_by("-created_at")[:20]

    datasets = DatasetVersion.objects.filter(
        dataset__workspace__in=workspaces
    ).select_related("dataset", "dataset__workspace").order_by("-uploaded_at")[:10]

    context = {
        "target_user": target_user,
        "profile": profile,
        "workspaces": workspaces,
        "dashboards": dashboards,
        "datasets": datasets,
        "plan_choices": [("free", "Free"), ("pro", "Pro"), ("enterprise", "Enterprise")],
        "plan_limits": PLAN_LIMITS,
        "sub_status_choices": ["active", "cancelled", "paused", "past_due", "expired"],
    }
    return render(request, "admin_portal/user_detail.html", context)


# ── Subscriptions ─────────────────────────────────────────────────────────────

@staff_required
def admin_subscriptions(request):
    status_filter = request.GET.get("status", "")
    plan_filter = request.GET.get("plan", "")
    q = request.GET.get("q", "").strip()

    qs = UserProfile.objects.select_related("user").order_by("-updated_at")

    if status_filter:
        if status_filter == "free":
            qs = qs.filter(plan="free")
        elif status_filter == "paid":
            qs = qs.filter(plan__in=["pro", "enterprise"])
        else:
            qs = qs.filter(ls_subscription_status=status_filter)

    if plan_filter:
        qs = qs.filter(plan=plan_filter)

    if q:
        qs = qs.filter(Q(user__username__icontains=q) | Q(user__email__icontains=q) | Q(ls_customer_id__icontains=q))

    # Summary counts
    summary = {
        "total": UserProfile.objects.count(),
        "free": UserProfile.objects.filter(plan="free").count(),
        "pro": UserProfile.objects.filter(plan="pro").count(),
        "enterprise": UserProfile.objects.filter(plan="enterprise").count(),
        "active": UserProfile.objects.filter(ls_subscription_status="active").count(),
        "cancelled": UserProfile.objects.filter(ls_subscription_status="cancelled").count(),
        "paused": UserProfile.objects.filter(ls_subscription_status="paused").count(),
        "past_due": UserProfile.objects.filter(ls_subscription_status="past_due").count(),
    }

    context = {
        "profiles": qs,
        "q": q,
        "status_filter": status_filter,
        "plan_filter": plan_filter,
        "summary": summary,
        "total_count": qs.count(),
    }
    return render(request, "admin_portal/subscriptions.html", context)


# ── Plans ─────────────────────────────────────────────────────────────────────

@staff_required
def admin_plans(request):
    # Distribution per plan with user count
    plan_stats = (
        UserProfile.objects
        .values("plan")
        .annotate(count=Count("id"))
        .order_by("plan")
    )
    plan_stats_map = {row["plan"]: row["count"] for row in plan_stats}

    # Active subscriptions per plan
    active_by_plan = (
        UserProfile.objects
        .filter(ls_subscription_status="active")
        .values("plan")
        .annotate(count=Count("id"))
    )
    active_map = {row["plan"]: row["count"] for row in active_by_plan}

    plans_with_stats = []
    for key, limits in PLAN_LIMITS.items():
        plans_with_stats.append({
            "key": key,
            "limits": limits,
            "user_count": plan_stats_map.get(key, 0),
            "active_sub_count": active_map.get(key, 0),
        })

    # Recent conversions (free → paid)
    recent_pro = (
        UserProfile.objects
        .filter(plan__in=["pro", "enterprise"])
        .select_related("user")
        .order_by("-updated_at")[:10]
    )

    # Churned (cancelled in last 30 days)
    churned = (
        UserProfile.objects
        .filter(
            ls_subscription_status="cancelled",
            updated_at__gte=timezone.now() - timedelta(days=30),
        )
        .select_related("user")
        .order_by("-updated_at")[:10]
    )

    # Chart: conversion funnel
    total_users = User.objects.count()
    funnel_json = json.dumps({
        "labels": ["Registered", "Created Dashboard", "Uploaded Data", "Paid"],
        "data": [
            total_users,
            Dashboard.objects.values("workspace__owner").distinct().count(),
            DatasetVersion.objects.values("dataset__workspace__owner").distinct().count(),
            plan_stats_map.get("pro", 0) + plan_stats_map.get("enterprise", 0),
        ],
    })

    context = {
        "plans": plans_with_stats,
        "recent_pro": recent_pro,
        "churned": churned,
        "funnel_json": funnel_json,
    }
    return render(request, "admin_portal/plans.html", context)
