import json
from pathlib import Path

import pandas as pd
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.datasets.models import DatasetVersion
from apps.datasets.services import (
    _bar_config,
    _line_config,
    _pie_config,
    build_profile_summary,
    generate_widget_specs_from_version,
)

from .models import Dashboard, DashboardShareLink, DashboardWidget

_FALLBACK_CHART = {
    "type": "bar",
    "data": {
        "labels": ["North", "South", "East", "West"],
        "datasets": [
            {
                "label": "Revenue",
                "data": [120, 95, 135, 88],
                "backgroundColor": ["#6366f1", "#8b5cf6", "#a78bfa", "#c4b5fd"],
                "borderRadius": 6,
            }
        ],
    },
    "options": {
        "responsive": True,
        "maintainAspectRatio": False,
        "plugins": {"legend": {"display": False}},
        "scales": {
            "x": {"grid": {"display": False}},
            "y": {"grid": {"color": "rgba(255,255,255,0.1)"}, "ticks": {"color": "#94a3b8"}},
        },
    },
}


def landing_page(request: HttpRequest) -> HttpResponse:
    context = {
        "chart_config": _FALLBACK_CHART,
        "mock_stats": [
            {"label": "Total Revenue", "value": "$48,200"},
            {"label": "Active Users", "value": "3,841"},
            {"label": "Conversion", "value": "12.4%"},
        ],
        "mock_table_rows": [
            {"width": 80},
            {"width": 65},
            {"width": 55},
            {"width": 40},
            {"width": 30},
        ],
        "trust_brands": ["Meridian Co.", "Apex Labs", "Strata Inc.", "Velox Group", "Orion Analytics"],
        "how_it_works": [
            {
                "num": "1",
                "title": "Upload your spreadsheet",
                "description": "Drag and drop any CSV, Excel, or JSON file. DashAI handles the rest — no data prep required.",
            },
            {
                "num": "2",
                "title": "Review AI-powered insights",
                "description": "DashAI profiles your data, detects column types, and suggests the best chart types for your dataset.",
            },
            {
                "num": "3",
                "title": "Share your dashboard",
                "description": "Generate a secure link and share with your team or stakeholders — no account needed to view.",
            },
        ],
        "testimonials": [
            {
                "quote": "I used to spend half a day building reports in Excel. Now I upload my CSV and share a link in two minutes.",
                "name": "Sarah Chen",
                "role": "Head of Analytics, Meridian Co.",
                "initials": "SC",
            },
            {
                "quote": "Our sales team finally has real-time visibility into their pipeline. DashAI made it stupidly easy.",
                "name": "James Patel",
                "role": "VP Sales, Apex Labs",
                "initials": "JP",
            },
            {
                "quote": "The AI suggestions are surprisingly accurate. It recommended the right chart on the first try every time.",
                "name": "Maria Santos",
                "role": "Data Analyst, Strata Inc.",
                "initials": "MS",
            },
        ],
    }
    return render(request, "landing.html", context)


def pricing_page(request: HttpRequest) -> HttpResponse:
    context = {
        "free_features": [
            "3 dashboards",
            "5 dataset uploads / month",
            "Unlimited share links",
            "CSV, Excel, JSON support",
            "Basic chart types (bar, line, pie)",
            "Community support",
        ],
        "pro_features": [
            "Unlimited dashboards",
            "Unlimited dataset uploads",
            "Advanced chart types",
            "Team workspace (up to 5 seats)",
            "Data refresh scheduling",
            "Priority email support",
            "Remove DashAI branding",
        ],
        "enterprise_features": [
            "Unlimited seats",
            "SSO / SAML authentication",
            "Custom integrations & API",
            "Dedicated account manager",
            "SLA guarantee",
            "On-premise deployment option",
        ],
        "faqs": [
            {
                "question": "Can I try DashAI before paying?",
                "answer": "Yes! The Starter plan is free forever with no credit card required. You get 3 dashboards and 5 uploads per month to explore the product.",
            },
            {
                "question": "What file types are supported?",
                "answer": "DashAI supports CSV, XLSX, XLSM, and JSON files on all plans. Enterprise customers can request additional connector types.",
            },
            {
                "question": "How are share links secured?",
                "answer": "Every share link uses a unique UUID token. Links can be revoked at any time from your dashboard settings.",
            },
            {
                "question": "Can I cancel my subscription at any time?",
                "answer": "Absolutely. Cancel anytime from your account settings with no penalty. You'll retain access until the end of your billing period.",
            },
        ],
    }
    return render(request, "pricing.html", context)


@login_required
def app_home(request: HttpRequest) -> HttpResponse:
    import hashlib
    from apps.datasets.models import Dataset

    all_dashboards = Dashboard.objects.filter(workspace__owner=request.user).order_by("-created_at")
    total_dashboards = all_dashboards.count()
    recent_dashboards = list(all_dashboards[:12])

    for dashboard in recent_dashboards:
        share = dashboard.share_links.filter(is_active=True).order_by("-created_at").first()
        dashboard.share_url = (
            request.build_absolute_uri(f"/dashboards/share/{share.token}/") if share else None
        )
        dashboard.widget_count = dashboard.widgets.count()
        seed = int(hashlib.md5(str(dashboard.id).encode()).hexdigest()[:8], 16)
        dashboard.preview_bars = [(seed >> (i * 4) & 0xF) * 5 + 20 for i in range(5)]

    workspace_qs = request.user.owned_workspaces.all()
    total_datasets = 0
    active_shares = 0
    if workspace_qs.exists():
        ws = workspace_qs.first()
        total_datasets = Dataset.objects.filter(workspace=ws).count()
        active_shares = DashboardShareLink.objects.filter(
            dashboard__workspace=ws, is_active=True
        ).count()

    stats = {
        "total_dashboards": total_dashboards,
        "total_datasets": total_datasets,
        "active_shares": active_shares,
    }

    return render(
        request,
        "dashboards/home.html",
        {
            "recent_dashboards": recent_dashboards,
            "stats": stats,
        },
    )


@login_required
def dashboard_detail(request: HttpRequest, dashboard_id: int) -> HttpResponse:
    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)
    widgets = dashboard.widgets.order_by("position")
    share_links = dashboard.share_links.filter(is_active=True).order_by("-created_at")
    chart_types = [
        ("bar", "📊", "Bar"),
        ("line", "📈", "Line"),
        ("pie", "🥧", "Pie"),
        ("kpi", "🔢", "KPI"),
    ]
    return render(
        request,
        "dashboards/detail.html",
        {
            "dashboard": dashboard,
            "widgets": widgets,
            "share_links": share_links,
            "chart_types": chart_types,
        },
    )


@login_required
def dashboard_create_from_version(request: HttpRequest, version_id: int) -> HttpResponse:
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    dataset_version = get_object_or_404(
        DatasetVersion,
        id=version_id,
        dataset__workspace__owner=request.user,
    )

    dashboard = Dashboard.objects.create(
        workspace=dataset_version.dataset.workspace,
        dataset_version=dataset_version,
        title=f"{dataset_version.dataset.name} Overview",
    )

    widget_specs = generate_widget_specs_from_version(dataset_version)

    if widget_specs:
        for spec in widget_specs:
            DashboardWidget.objects.create(
                dashboard=dashboard,
                title=spec["title"],
                widget_type=spec["widget_type"],
                position=spec["position"],
                chart_config=spec["config"],
            )
    else:
        # Fallback widgets when data cannot be parsed
        DashboardWidget.objects.create(
            dashboard=dashboard,
            title="Total Rows",
            widget_type=DashboardWidget.WidgetType.KPI,
            position=1,
            chart_config={"kpi": "rows", "value": f"{dataset_version.row_count:,}"},
        )
        DashboardWidget.objects.create(
            dashboard=dashboard,
            title="Top Categories",
            widget_type=DashboardWidget.WidgetType.BAR,
            position=2,
            chart_config=_FALLBACK_CHART,
        )

    return redirect("dashboard-detail", dashboard_id=dashboard.id)


@login_required
def dashboard_create_share_link(request: HttpRequest, dashboard_id: int) -> HttpResponse:
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)
    DashboardShareLink.objects.create(dashboard=dashboard)

    # Return to detail page if that's where the request came from
    referer = request.META.get("HTTP_REFERER", "")
    if f"/dashboards/{dashboard_id}/" in referer:
        return redirect("dashboard-detail", dashboard_id=dashboard_id)
    return redirect("app-home")


def dashboard_public_view(request: HttpRequest, token) -> HttpResponse:
    share_link = get_object_or_404(
        DashboardShareLink.objects.select_related("dashboard", "dashboard__dataset_version"),
        token=token,
        is_active=True,
    )
    dashboard = share_link.dashboard
    widgets = dashboard.widgets.order_by("position")
    return render(request, "dashboards/public_view.html", {"dashboard": dashboard, "widgets": widgets})


def _load_df_from_version(dataset_version) -> pd.DataFrame | None:
    """Load a DataFrame from a DatasetVersion's source file. Returns None on failure."""
    try:
        file_path = dataset_version.source_file.path
        name = Path(file_path).name.lower()
        if name.endswith(".csv"):
            return pd.read_csv(file_path)
        elif name.endswith((".xlsx", ".xlsm")):
            return pd.read_excel(file_path)
        elif name.endswith(".json"):
            return pd.read_json(file_path)
    except Exception:
        pass
    return None


@login_required
def dashboard_get_columns(request: HttpRequest, dashboard_id: int) -> JsonResponse:
    """Return column metadata for the dataset linked to a dashboard."""
    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)

    if not dashboard.dataset_version:
        return JsonResponse({"dimensions": [], "measures": [], "date_cols": [], "all_cols": []})

    df = _load_df_from_version(dashboard.dataset_version)
    if df is None:
        return JsonResponse({"dimensions": [], "measures": [], "date_cols": [], "all_cols": []})

    profile = build_profile_summary(df)
    date_cols = [c for c in df.columns if any(k in str(c).lower() for k in ["date", "month", "year", "period", "quarter"])]

    return JsonResponse({
        "dimensions": profile.categorical_columns,
        "measures": profile.numeric_columns,
        "date_cols": date_cols,
        "all_cols": [str(c) for c in df.columns],
    })


@login_required
def dashboard_add_widget(request: HttpRequest, dashboard_id: int) -> JsonResponse:
    """Create a new chart widget and append it to the dashboard."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    chart_type = data.get("chart_type", "").strip()
    title = data.get("title", "").strip() or "New Widget"
    dimension = data.get("dimension", "").strip()
    measure = data.get("measure", "").strip()
    preview_only = bool(data.get("preview_only", False))

    if chart_type not in ("bar", "line", "pie", "kpi"):
        return JsonResponse({"error": "Invalid chart type"}, status=400)

    config: dict = {}

    if chart_type == "kpi":
        if measure and dashboard.dataset_version:
            df = _load_df_from_version(dashboard.dataset_version)
            if df is not None and measure in df.columns:
                total = df[measure].sum()
                config = {"kpi": measure, "value": f"{total:,.0f}"}
            else:
                config = {"kpi": measure, "value": "N/A"}
        else:
            config = {"kpi": "value", "value": "0"}

    else:
        if not dashboard.dataset_version:
            return JsonResponse({"error": "Dashboard has no dataset"}, status=400)

        df = _load_df_from_version(dashboard.dataset_version)
        if df is None:
            return JsonResponse({"error": "Could not load dataset file"}, status=500)

        try:
            if chart_type == "bar":
                if not dimension or not measure:
                    return JsonResponse({"error": "dimension and measure are required for bar charts"}, status=400)
                top = df.groupby(dimension)[measure].sum().nlargest(10)
                labels = [str(l) for l in top.index.tolist()]
                values = [round(float(v), 2) for v in top.values.tolist()]
                config = _bar_config(labels, values, measure)

            elif chart_type == "line":
                if not dimension or not measure:
                    return JsonResponse({"error": "dimension and measure are required for line charts"}, status=400)
                tmp = df[[dimension, measure]].copy()
                try:
                    tmp[dimension] = pd.to_datetime(tmp[dimension], errors="coerce")
                    tmp = tmp.dropna(subset=[dimension])
                    trend = tmp.groupby(tmp[dimension].dt.to_period("M"))[measure].sum()
                except Exception:
                    trend = tmp.groupby(dimension)[measure].sum()
                labels = [str(p) for p in trend.index.tolist()]
                values = [round(float(v), 2) for v in trend.values.tolist()]
                config = _line_config(labels, values, measure)

            elif chart_type == "pie":
                if not dimension:
                    return JsonResponse({"error": "dimension is required for pie charts"}, status=400)
                if measure and measure in df.columns:
                    vc = df.groupby(dimension)[measure].sum().nlargest(6)
                else:
                    vc = df[dimension].value_counts().head(6)
                labels = [str(l) for l in vc.index.tolist()]
                values = [round(float(v), 2) for v in vc.values.tolist()]
                config = _pie_config(labels, values)

        except Exception as exc:
            return JsonResponse({"error": str(exc)}, status=500)

    if preview_only:
        return JsonResponse({"success": True, "chart_config": config})

    max_pos = dashboard.widgets.order_by("-position").values_list("position", flat=True).first() or 0
    widget = DashboardWidget.objects.create(
        dashboard=dashboard,
        title=title,
        widget_type=chart_type,
        position=max_pos + 1,
        chart_config=config,
    )

    return JsonResponse({"success": True, "widget_id": widget.id, "chart_config": config})


@login_required
def dashboard_delete_widget(request: HttpRequest, dashboard_id: int, widget_id: int) -> JsonResponse:
    """Delete a single widget from a dashboard."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)
    widget = get_object_or_404(DashboardWidget, id=widget_id, dashboard=dashboard)
    widget.delete()

    return JsonResponse({"success": True})
