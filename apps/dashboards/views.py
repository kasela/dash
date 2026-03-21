import json
from pathlib import Path

import pandas as pd
from django.db import models
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.datasets.models import DatasetVersion
from apps.datasets.services import (
    PALETTES,
    _area_config,
    _bar_config,
    _doughnut_config,
    _hbar_config,
    _line_config,
    _multi_bar_config,
    _multi_line_config,
    _pie_config,
    _radar_config,
    _scatter_config,
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

_VALID_CHART_TYPES = {"bar", "line", "pie", "kpi", "doughnut", "area", "hbar", "scatter", "radar", "table"}


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
    from apps.billing.models import UserProfile
    UserProfile.objects.get_or_create(user=request.user)  # ensure profile exists

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
        ("bar",      "📊", "Bar"),
        ("line",     "📈", "Line"),
        ("area",     "🏔️", "Area"),
        ("pie",      "🥧", "Pie"),
        ("doughnut", "🍩", "Doughnut"),
        ("hbar",     "↔️", "Horiz. Bar"),
        ("scatter",  "✦",  "Scatter"),
        ("radar",    "🕸️", "Radar"),
        ("table",    "🧾", "Table"),
        ("kpi",      "🔢", "KPI"),
    ]
    palette_names = list(PALETTES.keys())
    return render(
        request,
        "dashboards/detail.html",
        {
            "dashboard": dashboard,
            "widgets": widgets,
            "share_links": share_links,
            "chart_types": chart_types,
            "palette_names": palette_names,
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

    # Enforce dashboard limit for free plan
    from apps.billing.models import UserProfile
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if not profile.is_pro:
        current_count = Dashboard.objects.filter(workspace__owner=request.user).count()
        if current_count >= profile.max_dashboards:
            from django.contrib import messages
            messages.error(
                request,
                f"You've reached the {profile.max_dashboards} dashboard limit on the Free plan. "
                "Upgrade to Pro for unlimited dashboards."
            )
            return redirect("app-home")

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

    result = _build_widget_config(dashboard, data)
    if result.get("error"):
        return JsonResponse({"error": result["error"]}, status=result.get("status", 400))
    chart_type = result["chart_type"]
    title = result["title"]
    config = result["config"]
    preview_only = result["preview_only"]

    if preview_only:
        return JsonResponse({"success": True, "chart_config": config})

    max_pos = dashboard.widgets.order_by("-position").values_list("position", flat=True).first() or 0
    # Map area -> line type for widget_type storage (area uses line Chart.js type)
    stored_type = chart_type
    widget = DashboardWidget.objects.create(
        dashboard=dashboard,
        title=title,
        widget_type=stored_type,
        position=max_pos + 1,
        chart_config=config,
    )

    return JsonResponse({"success": True, "widget_id": widget.id, "chart_config": config})


@login_required
def dashboard_add_heading(request: HttpRequest, dashboard_id: int) -> JsonResponse:
    """Create a text heading widget at a user-selected location."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    text = str(data.get("text", "")).strip()
    if not text:
        return JsonResponse({"error": "Heading text is required"}, status=400)
    if len(text) > 200:
        return JsonResponse({"error": "Heading text too long (max 200 chars)"}, status=400)

    font_size = str(data.get("font_size", "2xl")).strip().lower()
    if font_size not in {"lg", "xl", "2xl", "3xl"}:
        font_size = "2xl"
    color = str(data.get("color", "indigo")).strip().lower()
    if color not in {"slate", "indigo", "emerald", "rose", "amber"}:
        color = "indigo"
    font_family = str(data.get("font_family", "inter")).strip().lower()
    if font_family not in {"inter", "poppins", "serif", "mono"}:
        font_family = "inter"
    align = str(data.get("align", "left")).strip().lower()
    if align not in {"left", "center", "right"}:
        align = "left"

    after_widget_id = data.get("after_widget_id")
    insert_pos = 1
    if after_widget_id in (None, "", 0, "0"):
        insert_pos = 1
    else:
        try:
            after_id = int(after_widget_id)
        except (TypeError, ValueError):
            return JsonResponse({"error": "Invalid after_widget_id"}, status=400)
        after_widget = get_object_or_404(DashboardWidget, id=after_id, dashboard=dashboard)
        insert_pos = after_widget.position + 1

    dashboard.widgets.filter(position__gte=insert_pos).update(position=models.F("position") + 1)
    config = {
        "text": text,
        "font_size": font_size,
        "color": color,
        "font_family": font_family,
        "align": align,
        "layout": {"size": "lg"},
    }
    widget = DashboardWidget.objects.create(
        dashboard=dashboard,
        title=text[:80],
        widget_type="heading",
        position=insert_pos,
        chart_config=config,
    )
    return JsonResponse({"success": True, "widget_id": widget.id})


def _build_widget_config(dashboard: Dashboard, data: dict) -> dict:
    chart_type = data.get("chart_type", "").strip()
    title = data.get("title", "").strip() or "New Widget"
    dimension = data.get("dimension", "").strip()
    raw_measures = data.get("measures", data.get("measure", ""))
    if isinstance(raw_measures, list):
        measures = [m.strip() for m in raw_measures if m.strip()]
    else:
        measures = [raw_measures.strip()] if raw_measures else []
    measure = measures[0] if measures else ""
    raw_table_columns = data.get("table_columns", [])
    if isinstance(raw_table_columns, list):
        table_columns = [c.strip() for c in raw_table_columns if isinstance(c, str) and c.strip()]
    elif isinstance(raw_table_columns, str):
        table_columns = [raw_table_columns.strip()] if raw_table_columns.strip() else []
    else:
        table_columns = []
    raw_group_by = data.get("group_by", [])
    if isinstance(raw_group_by, list):
        group_by = [c.strip() for c in raw_group_by if isinstance(c, str) and c.strip()]
    elif isinstance(raw_group_by, str):
        group_by = [raw_group_by.strip()] if raw_group_by.strip() else []
    else:
        group_by = []
    x_measure = data.get("x_measure", "").strip()
    y_measure = data.get("y_measure", "").strip()
    x_label = data.get("x_label", "").strip()
    y_label = data.get("y_label", "").strip()
    palette = data.get("palette", "indigo").strip()
    if palette not in PALETTES:
        palette = "indigo"
    preview_only = bool(data.get("preview_only", False))

    if chart_type not in _VALID_CHART_TYPES:
        return {"error": "Invalid chart type", "status": 400}

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
            return {"error": "Dashboard has no dataset", "status": 400}
        df = _load_df_from_version(dashboard.dataset_version)
        if df is None:
            return {"error": "Could not load dataset file", "status": 500}
        try:
            if chart_type == "bar":
                if not dimension or not measures:
                    return {"error": "dimension and at least one measure are required for bar charts", "status": 400}
                if len(measures) == 1:
                    top = df.groupby(dimension)[measure].sum().nlargest(10)
                    config = _bar_config([str(l) for l in top.index.tolist()], [round(float(v), 2) for v in top.values.tolist()], measure, palette, x_label, y_label)
                else:
                    all_cats = [str(c) for c in df[dimension].dropna().unique()[:15]]
                    datasets = []
                    for m in measures:
                        if m not in df.columns:
                            continue
                        grp = df.groupby(dimension)[m].sum()
                        datasets.append({"label": m, "data": [round(float(grp.get(c, 0)), 2) for c in all_cats]})
                    config = _multi_bar_config(all_cats, datasets, palette, x_label, y_label)
            elif chart_type == "hbar":
                if not dimension or not measure:
                    return {"error": "dimension and measure are required for horizontal bar charts", "status": 400}
                top = df.groupby(dimension)[measure].sum().nlargest(10)
                config = _hbar_config([str(l) for l in top.index.tolist()], [round(float(v), 2) for v in top.values.tolist()], measure, palette, x_label, y_label)
            elif chart_type == "line":
                if not dimension or not measures:
                    return {"error": "dimension and at least one measure are required for line charts", "status": 400}
                if len(measures) == 1:
                    tmp = df[[dimension, measure]].copy()
                    try:
                        tmp[dimension] = pd.to_datetime(tmp[dimension], errors="coerce")
                        tmp = tmp.dropna(subset=[dimension])
                        trend = tmp.groupby(tmp[dimension].dt.to_period("M"))[measure].sum()
                    except Exception:
                        trend = tmp.groupby(dimension)[measure].sum()
                    config = _line_config([str(p) for p in trend.index.tolist()], [round(float(v), 2) for v in trend.values.tolist()], measure, palette, x_label, y_label)
                else:
                    all_cats = None
                    datasets = []
                    for m in measures:
                        if m not in df.columns:
                            continue
                        tmp = df[[dimension, m]].copy()
                        try:
                            tmp[dimension] = pd.to_datetime(tmp[dimension], errors="coerce")
                            tmp = tmp.dropna(subset=[dimension])
                            trend = tmp.groupby(tmp[dimension].dt.to_period("M"))[m].sum()
                        except Exception:
                            trend = tmp.groupby(dimension)[m].sum()
                        if all_cats is None:
                            all_cats = [str(p) for p in trend.index.tolist()]
                        datasets.append({"label": m, "data": [round(float(v), 2) for v in trend.values.tolist()]})
                    config = _multi_line_config(all_cats or [], datasets, palette, x_label, y_label)
            elif chart_type == "area":
                if not dimension or not measure:
                    return {"error": "dimension and measure are required for area charts", "status": 400}
                tmp = df[[dimension, measure]].copy()
                try:
                    tmp[dimension] = pd.to_datetime(tmp[dimension], errors="coerce")
                    tmp = tmp.dropna(subset=[dimension])
                    trend = tmp.groupby(tmp[dimension].dt.to_period("M"))[measure].sum()
                except Exception:
                    trend = tmp.groupby(dimension)[measure].sum()
                config = _area_config([str(p) for p in trend.index.tolist()], [round(float(v), 2) for v in trend.values.tolist()], measure, palette, x_label, y_label)
            elif chart_type == "pie":
                if not dimension:
                    return {"error": "dimension is required for pie charts", "status": 400}
                vc = df.groupby(dimension)[measure].sum().nlargest(6) if measure and measure in df.columns else df[dimension].value_counts().head(6)
                config = _pie_config([str(l) for l in vc.index.tolist()], [round(float(v), 2) for v in vc.values.tolist()], palette)
            elif chart_type == "doughnut":
                if not dimension:
                    return {"error": "dimension is required for doughnut charts", "status": 400}
                vc = df.groupby(dimension)[measure].sum().nlargest(6) if measure and measure in df.columns else df[dimension].value_counts().head(6)
                config = _doughnut_config([str(l) for l in vc.index.tolist()], [round(float(v), 2) for v in vc.values.tolist()], palette)
            elif chart_type == "scatter":
                if not x_measure or not y_measure:
                    return {"error": "x_measure and y_measure are required for scatter charts", "status": 400}
                if x_measure not in df.columns or y_measure not in df.columns:
                    return {"error": "Selected columns not found in dataset", "status": 400}
                tmp = df[[x_measure, y_measure]].dropna().head(500)
                config = _scatter_config([round(float(v), 4) for v in tmp[x_measure].tolist()], [round(float(v), 4) for v in tmp[y_measure].tolist()], x_measure, y_measure, palette, f"{x_measure} vs {y_measure}")
            elif chart_type == "radar":
                if not dimension or not measure:
                    return {"error": "dimension and measure are required for radar charts", "status": 400}
                top = df.groupby(dimension)[measure].sum().nlargest(8)
                config = _radar_config([str(l) for l in top.index.tolist()], [round(float(v), 2) for v in top.values.tolist()], measure, palette)
            elif chart_type == "table":
                columns = [c for c in table_columns if c in df.columns]
                if not columns:
                    columns = [c for c in ([dimension] + measures) if c and c in df.columns]
                if not columns:
                    columns = [str(c) for c in df.columns[:5]]
                table_df = df[columns].copy()
                valid_group_by = []
                for col in group_by:
                    if col in table_df.columns and col not in valid_group_by:
                        valid_group_by.append(col)
                if valid_group_by:
                    numeric_agg_cols = [
                        col for col in table_df.columns
                        if col not in valid_group_by and pd.api.types.is_numeric_dtype(table_df[col])
                    ]
                    if numeric_agg_cols:
                        grouped = table_df.groupby(valid_group_by, dropna=False)[numeric_agg_cols].sum().reset_index()
                    else:
                        grouped = table_df.groupby(valid_group_by, dropna=False).size().reset_index(name="row_count")
                    table_df = grouped
                preview_df = table_df.head(100).fillna("")
                rows = [[str(v) for v in row] for row in preview_df.values.tolist()]
                config = {
                    "columns": [str(c) for c in preview_df.columns.tolist()],
                    "rows": rows,
                    "group_by": valid_group_by,
                }
        except Exception as exc:
            return {"error": str(exc), "status": 500}
    if isinstance(config, dict):
        config["builder"] = {
            "dimension": dimension,
            "measures": measures,
            "measure": measure,
            "x_measure": x_measure,
            "y_measure": y_measure,
            "x_label": x_label,
            "y_label": y_label,
            "palette": palette,
            "table_columns": table_columns,
            "group_by": group_by,
        }
    return {"chart_type": chart_type, "title": title, "config": config, "preview_only": preview_only}


@login_required
def dashboard_update_widget(request: HttpRequest, dashboard_id: int, widget_id: int) -> JsonResponse:
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)
    widget = get_object_or_404(DashboardWidget, id=widget_id, dashboard=dashboard)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    result = _build_widget_config(dashboard, data)
    if result.get("error"):
        return JsonResponse({"error": result["error"]}, status=result.get("status", 400))
    widget.title = result["title"]
    widget.widget_type = result["chart_type"]
    widget.chart_config = result["config"]
    widget.save(update_fields=["title", "widget_type", "chart_config"])
    return JsonResponse({"success": True, "widget_id": widget.id, "chart_config": widget.chart_config})


@login_required
def dashboard_resize_widget(request: HttpRequest, dashboard_id: int, widget_id: int) -> JsonResponse:
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)
    widget = get_object_or_404(DashboardWidget, id=widget_id, dashboard=dashboard)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    size = data.get("size", "md").strip().lower()
    if size not in {"sm", "md", "lg"}:
        return JsonResponse({"error": "Invalid size"}, status=400)
    raw_height = data.get("height")
    height = None
    if raw_height is not None:
        try:
            height = int(raw_height)
        except (TypeError, ValueError):
            return JsonResponse({"error": "Invalid height"}, status=400)
        if height < 180 or height > 900:
            return JsonResponse({"error": "Height out of range"}, status=400)
    cfg = widget.chart_config or {}
    layout = cfg.get("layout", {})
    layout["size"] = size
    if height is not None:
        layout["height"] = height
    cfg["layout"] = layout
    widget.chart_config = cfg
    widget.save(update_fields=["chart_config"])
    return JsonResponse({"success": True, "size": size, "height": layout.get("height")})


@login_required
def dashboard_rename(request: HttpRequest, dashboard_id: int) -> JsonResponse:
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    new_title = data.get("title", "").strip()
    if not new_title:
        return JsonResponse({"error": "Title cannot be empty"}, status=400)
    if len(new_title) > 200:
        return JsonResponse({"error": "Title too long (max 200 chars)"}, status=400)
    dashboard.title = new_title
    dashboard.save(update_fields=["title"])
    return JsonResponse({"success": True, "title": dashboard.title})


@login_required
def dashboard_delete_widget(request: HttpRequest, dashboard_id: int, widget_id: int) -> JsonResponse:
    """Delete a single widget from a dashboard."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)
    widget = get_object_or_404(DashboardWidget, id=widget_id, dashboard=dashboard)
    widget.delete()

    return JsonResponse({"success": True})


@login_required
def dashboard_rename_widget(request: HttpRequest, dashboard_id: int, widget_id: int) -> JsonResponse:
    """Rename a widget title."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)
    widget = get_object_or_404(DashboardWidget, id=widget_id, dashboard=dashboard)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    new_title = data.get("title", "").strip()
    if not new_title:
        return JsonResponse({"error": "Title cannot be empty"}, status=400)
    if len(new_title) > 200:
        return JsonResponse({"error": "Title too long (max 200 chars)"}, status=400)

    widget.title = new_title
    widget.save(update_fields=["title"])

    return JsonResponse({"success": True, "title": widget.title})
