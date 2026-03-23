import json
import math
from pathlib import Path
import re
import importlib.util
import warnings

import pandas as pd
from django.conf import settings
from django.db import models
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.datasets.models import DatasetVersion
from apps.datasets.services import (
    PALETTES,
    _area_config,
    _bar_config,
    _bubble_config,
    _doughnut_config,
    _funnel_config,
    _gauge_config,
    _hbar_config,
    _line_config,
    _mixed_config,
    _multi_bar_config,
    _multi_line_config,
    _pie_config,
    _polararea_config,
    _radar_config,
    _scatter_config,
    _waterfall_config,
    apply_df_filters,
    build_profile_summary,
    generate_widget_specs_from_version,
    ai_clean_dataframe,
    ai_suggest_slicers,
    ai_analyze_chart,
    ai_generate_dashboard_specs,
    ai_generate_dashboard_title,
    ai_generate_executive_summary,
    ai_detect_column_roles,
    ai_generate_comprehensive_insights,
    ai_generate_html_dashboard,
    _get_ai_client,
    _compute_kpi_trend,
    _detect_kpi_meta,
    _humanize_col,
)

from .models import Dashboard, DashboardDataset, DashboardShareLink, DashboardWidget

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

_VALID_CHART_TYPES = {
    "bar", "line", "pie", "kpi", "doughnut", "area", "hbar", "scatter", "radar", "table", "map", "smart",
    # Pro/Plus chart types
    "bubble", "polararea", "mixed", "funnel", "gauge", "waterfall",
}

_PRO_CHART_TYPES = {"bubble", "polararea", "mixed", "funnel", "gauge", "waterfall"}


def _to_datetime_safely(series: pd.Series) -> pd.Series:
    """Parse date-like series while avoiding noisy format-inference warnings."""
    try:
        return pd.to_datetime(series, errors="coerce", format="mixed")
    except TypeError:
        # Older pandas versions may not support format="mixed".
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Could not infer format", category=UserWarning)
            return pd.to_datetime(series, errors="coerce")


def _fallback_smart_chart(df: pd.DataFrame, prompt: str) -> dict:
    """Heuristic fallback used when DeepSeek is not configured/available."""
    profile = build_profile_summary(df)
    dims = [str(c) for c in profile.categorical_columns]
    nums = [str(c) for c in profile.numeric_columns]
    if dims and nums:
        return {"chart_type": "bar", "dimension": dims[0], "measures": [nums[0]], "title": prompt or f"{nums[0]} by {dims[0]}"}
    if len(nums) >= 2:
        return {"chart_type": "scatter", "x_measure": nums[0], "y_measure": nums[1], "title": prompt or f"{nums[0]} vs {nums[1]}"}
    if nums:
        return {"chart_type": "kpi", "measures": [nums[0]], "title": prompt or f"Total {nums[0]}"}
    if dims:
        return {"chart_type": "pie", "dimension": dims[0], "title": prompt or f"Distribution: {dims[0]}"}
    return {"chart_type": "table", "title": prompt or "Smart Table"}


def _infer_intent_hints(prompt: str) -> list[str]:
    """Infer chart intent hints from prompt keywords to steer smart chart selection."""
    text = (prompt or "").strip().lower()
    if not text:
        return []
    hints: list[str] = []
    keyword_map = [
        ("trend", ["trend", "over time", "monthly", "weekly", "daily", "timeline", "seasonality"]),
        ("comparison", ["compare", "comparison", "top", "rank", "ranking", "best", "worst"]),
        ("composition", ["share", "breakdown", "mix", "composition", "part of whole"]),
        ("relationship", ["correlation", "relationship", "impact", "influence", "driver"]),
        ("variance", ["variance", "change", "delta", "difference", "vs", "versus"]),
        ("conversion", ["funnel", "conversion", "drop-off", "dropoff", "pipeline", "stage"]),
        ("forecast", ["forecast", "predict", "projection", "outlook", "plan"]),
        ("anomaly", ["anomaly", "outlier", "spike", "dip", "unexpected", "abnormal"]),
        ("performance", ["performance", "efficiency", "productivity", "target", "goal"]),
    ]
    for hint, keywords in keyword_map:
        if any(k in text for k in keywords):
            hints.append(hint)
    return hints


def _normalize_smart_recommendation(parsed: dict, profile, clean_prompt: str) -> dict:
    """Fill missing chart fields using dataset profile so advanced chart recommendations remain valid."""
    dims = [str(c) for c in profile.categorical_columns]
    nums = [str(c) for c in profile.numeric_columns]

    rec_type = str(parsed.get("chart_type", "bar")).strip().lower()
    if rec_type not in _VALID_CHART_TYPES or rec_type == "smart":
        rec_type = "bar"

    rec_dimension = str(parsed.get("dimension", "")).strip()
    rec_measures = parsed.get("measures", [])
    if isinstance(rec_measures, str):
        rec_measures = [rec_measures]
    if not isinstance(rec_measures, list):
        rec_measures = []
    rec_measures = [str(m).strip() for m in rec_measures if str(m).strip()]
    rec_x = str(parsed.get("x_measure", "")).strip()
    rec_y = str(parsed.get("y_measure", "")).strip()
    rec_title = str(parsed.get("title", "")).strip() or clean_prompt

    if not rec_dimension and dims and rec_type in {"bar", "line", "area", "pie", "doughnut", "hbar", "radar", "table", "polararea", "funnel", "waterfall", "mixed"}:
        rec_dimension = dims[0]
    if not rec_measures and nums and rec_type in {"bar", "line", "area", "hbar", "radar", "kpi", "pie", "table", "polararea", "funnel", "gauge", "waterfall", "mixed"}:
        rec_measures = [nums[0]]
    if (not rec_x or not rec_y) and len(nums) >= 2 and rec_type in {"scatter", "bubble", "map"}:
        rec_x = rec_x or nums[0]
        rec_y = rec_y or nums[1]
    if rec_type == "kpi" and not rec_measures and nums:
        rec_measures = [nums[0]]

    return {
        "chart_type": rec_type,
        "dimension": rec_dimension,
        "measures": rec_measures,
        "x_measure": rec_x,
        "y_measure": rec_y,
        "title": rec_title,
    }


def _ai_smart_chart(df: pd.DataFrame, prompt: str) -> dict:
    """Ask the configured AI provider for best chart + fields; returns normalized recommendation."""
    clean_prompt = (prompt or "").strip()
    if not clean_prompt:
        clean_prompt = "Suggest the best chart for this dataset."
    profile = build_profile_summary(df)
    payload = {
        "prompt": clean_prompt,
        "intent_hints": _infer_intent_hints(clean_prompt),
        "columns": [str(c) for c in df.columns.tolist()[:200]],
        "dimensions": [str(c) for c in profile.categorical_columns[:80]],
        "measures": [str(c) for c in profile.numeric_columns[:80]],
        "allowed_chart_types": sorted(list(_VALID_CHART_TYPES - {"smart"})),
        "sample_rows": min(int(len(df.index)), 50000),
    }
    client, model = _get_ai_client()
    if client is None:
        return _fallback_smart_chart(df, clean_prompt)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert BI visualization assistant. Your goal is to recommend the most "
                        "informative chart for the user's analysis intent, not just any valid chart.\n"
                        "Return ONLY valid JSON (no markdown, no prose) with keys: "
                        "chart_type, title, dimension, measures, x_measure, y_measure, suggestions.\n"
                        "Rules:\n"
                        "1) chart_type must be one of allowed_chart_types.\n"
                        "2) Use only provided column names.\n"
                        "3) Match chart to analytical intent (trend/comparison/distribution/composition/relationship).\n"
                        "3b) Prioritize decision utility: choose charts that make next actions obvious.\n"
                        "4) Prefer line/area for time trends, bar/hbar for ranked comparisons, "
                        "scatter/bubble for relationships, pie/doughnut/polararea for simple part-to-whole with "
                        "limited categories, KPI for single headline metrics, table for detail lookups.\n"
                        "5) Avoid cluttered charts: if too many categories for pie-style views, choose bar or table.\n"
                        "6) If prompt is vague, choose the most broadly useful and interpretable chart from available fields.\n"
                        "7) Build a concise action-oriented title (<= 70 chars).\n"
                        "8) suggestions should be a short list (max 3) of practical follow-up chart ideas as strings.\n"
                        "9) If prompt mentions strategy/decision/action, bias toward variance, rank, and trend views.\n"
                        "10) Prefer advanced charts when intent matches: funnel (stage drop-offs), waterfall (variance bridge), "
                        "mixed (combo trend+bars), bubble (segmentation by 3 variables), gauge (single KPI vs target).\n"
                        "11) Never choose advanced charts unless required fields exist (e.g., funnel/waterfall need dimension+measure; "
                        "bubble needs x+y numeric; gauge needs one numeric).\n"
                        "12) Optimize for modern executive dashboards: concise titles, clear comparisons, actionable framing."
                    ),
                },
                {"role": "user", "content": json.dumps(payload)},
            ],
            temperature=0.2,
            stream=False,
            timeout=12,
        )
        content = ((response.choices[0].message.content) or "").strip()
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        parsed = json.loads(match.group(0) if match else content)
    except Exception:
        return _fallback_smart_chart(df, clean_prompt)
    return _normalize_smart_recommendation(parsed, profile, clean_prompt)


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
        "light_features": [
            "10 dashboards",
            "25 dataset uploads / month",
            "All basic chart types",
            "Email support",
            "Custom chart colors",
        ],
        "plus_features": [
            "50 dashboards",
            "100 dataset uploads / month",
            "Advanced chart types",
            "Team workspace (up to 3 seats)",
            "Data refresh scheduling",
            "Priority email support",
        ],
        "pro_features": [
            "Unlimited dashboards",
            "Unlimited dataset uploads",
            "All chart types incl. Pro charts",
            "Unlimited team seats",
            "Remove DashAI branding",
            "Dedicated account manager",
            "SLA guarantee",
        ],
        "faqs": [
            {
                "question": "Can I try DashAI before paying?",
                "answer": "Yes! The Free plan is free forever with no credit card required. You get 3 dashboards and 5 uploads per month to explore the product.",
            },
            {
                "question": "What file types are supported?",
                "answer": "DashAI supports CSV, XLSX, XLSM, and JSON files on all plans.",
            },
            {
                "question": "How are share links secured?",
                "answer": "Every share link uses a unique UUID token. Links can be revoked at any time from your dashboard settings.",
            },
            {
                "question": "Can I cancel my subscription at any time?",
                "answer": "Absolutely. Cancel anytime from your account settings with no penalty. You'll retain access until the end of your billing period.",
            },
            {
                "question": "What is the difference between monthly and annual billing?",
                "answer": "Annual billing saves you 20% compared to monthly. You are billed once per year at the discounted rate.",
            },
        ],
    }
    # Pass current user plan for highlighting
    current_plan = "free"
    if request.user.is_authenticated:
        try:
            from apps.billing.models import UserProfile
            profile, _ = UserProfile.objects.get_or_create(user=request.user)
            current_plan = profile.plan
        except Exception:
            pass
    context["current_plan"] = current_plan

    return render(request, "pricing.html", context)


# ── Static marketing pages ──────────────────────────────────────────────────────

def about_page(request: HttpRequest) -> HttpResponse:
    return render(request, "pages/about.html")


def blog_page(request: HttpRequest) -> HttpResponse:
    return render(request, "pages/blog.html")


def privacy_page(request: HttpRequest) -> HttpResponse:
    return render(request, "pages/privacy.html")


def terms_page(request: HttpRequest) -> HttpResponse:
    return render(request, "pages/terms.html")


def security_page(request: HttpRequest) -> HttpResponse:
    return render(request, "pages/security.html")


def contact_page(request: HttpRequest) -> HttpResponse:
    from django.core.mail import send_mail
    from django.contrib import messages as django_messages

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        email = request.POST.get("email", "").strip()
        subject = request.POST.get("subject", "").strip()
        message = request.POST.get("message", "").strip()

        if name and email and subject and message:
            try:
                send_mail(
                    subject=f"[DashAI Contact] {subject}",
                    message=f"From: {name} <{email}>\n\n{message}",
                    from_email="noreply@dashai.io",
                    recipient_list=["hello@dashai.io"],
                    fail_silently=True,
                )
            except Exception:
                pass
            return render(request, "pages/contact.html", {"success": True})
        else:
            return render(request, "pages/contact.html", {
                "error": "Please fill in all fields.",
                "form_data": {"name": name, "email": email, "subject": subject, "message": message},
            })

    initial_email = request.user.email if request.user.is_authenticated else ""
    return render(request, "pages/contact.html", {"initial_email": initial_email})


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
def dashboard_detail(request: HttpRequest, dashboard_id) -> HttpResponse:
    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)
    widgets = dashboard.widgets.order_by("position")
    share_links = dashboard.share_links.filter(is_active=True).order_by("-created_at")

    from apps.billing.models import UserProfile
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    is_pro = profile.is_pro

    # (type_key, icon, label, pro_required)
    chart_types = [
        ("smart",     "🤖", "Smart AI",   False),
        ("bar",       "📊", "Bar",        False),
        ("line",      "📈", "Line",       False),
        ("area",      "🏔️", "Area",       False),
        ("pie",       "🥧", "Pie",        False),
        ("doughnut",  "🍩", "Doughnut",   False),
        ("hbar",      "↔️", "Horiz. Bar", False),
        ("scatter",   "✦",  "Scatter",    False),
        ("map",       "🗺️", "Map",        False),
        ("radar",     "🕸️", "Radar",      False),
        ("table",     "🧾", "Table",      False),
        ("kpi",       "🔢", "KPI",        False),
        ("bubble",    "🫧", "Bubble",     True),
        ("polararea", "🎯", "Polar Area", True),
        ("mixed",     "📉", "Mixed",      True),
        ("funnel",    "🔽", "Funnel",     True),
        ("gauge",     "⏱️", "Gauge",      True),
        ("waterfall", "📶", "Waterfall",  True),
    ]
    palette_names = list(PALETTES.keys())
    # Build list of all datasets linked to this dashboard (primary + extras)
    linked_datasets = []
    primary_id = dashboard.dataset_version_id
    if dashboard.dataset_version:
        dv = dashboard.dataset_version
        linked_datasets.append({
            "version_id": dv.id,
            "label": dv.dataset.name,
            "row_count": dv.row_count,
            "column_count": dv.column_count,
            "is_primary": True,
        })
    for link in dashboard.dataset_links.select_related("dataset_version__dataset").order_by("added_at"):
        if link.dataset_version_id == primary_id:
            continue
        dv = link.dataset_version
        linked_datasets.append({
            "version_id": dv.id,
            "label": link.label or dv.dataset.name,
            "row_count": dv.row_count,
            "column_count": dv.column_count,
            "is_primary": False,
        })

    # Available workspace datasets (for "add dataset" picker)
    from apps.datasets.models import DatasetVersion as DV
    linked_version_ids = {d["version_id"] for d in linked_datasets}
    available_versions = list(
        DV.objects.filter(dataset__workspace=dashboard.workspace)
        .select_related("dataset")
        .order_by("-uploaded_at")[:50]
    )
    available_versions = [v for v in available_versions if v.id not in linked_version_ids]

    return render(
        request,
        "dashboards/detail.html",
        {
            "dashboard": dashboard,
            "widgets": widgets,
            "share_links": share_links,
            "chart_types": chart_types,
            "palette_names": palette_names,
            "linked_datasets": linked_datasets,
            "available_versions": available_versions,
            "is_pro": is_pro,
            "filter_config": dashboard.filter_config or [],
        },
    )


@login_required
def dashboard_create_from_version(request: HttpRequest, version_id: int) -> HttpResponse:
    """Create a dashboard shell immediately and dispatch chart-building to Celery.

    The user is redirected to the dashboard detail page straight away.
    Charts are lazily loaded in the browser as the Celery task progresses.
    """
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    dataset_version = get_object_or_404(
        DatasetVersion,
        id=version_id,
        dataset__workspace__owner=request.user,
    )

    # Enforce dashboard limit for free plan
    from apps.billing.models import UserProfile
    billing_profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if not billing_profile.is_pro:
        current_count = Dashboard.objects.filter(workspace__owner=request.user).count()
        if current_count >= billing_profile.max_dashboards:
            from django.contrib import messages
            messages.error(
                request,
                f"You've reached the {billing_profile.max_dashboards} dashboard limit on the Free plan. "
                "Upgrade to Pro for unlimited dashboards."
            )
            return redirect("app-home")

    # Create the dashboard shell immediately (status=pending)
    ai_title = None
    try:
        seed_df = _load_df_from_version(dataset_version)
        if seed_df is not None and len(seed_df.columns) > 0:
            seed_profile = build_profile_summary(seed_df)
            ai_title = ai_generate_dashboard_title(
                seed_df,
                seed_profile,
                dataset_name=dataset_version.dataset.name,
            )
    except Exception:
        ai_title = None

    dashboard = Dashboard.objects.create(
        workspace=dataset_version.dataset.workspace,
        dataset_version=dataset_version,
        title=(ai_title or _humanize_col(dataset_version.dataset.name)),
        build_status=Dashboard.BuildStatus.PENDING,
    )
    # Link as the primary dataset
    DashboardDataset.objects.get_or_create(
        dashboard=dashboard,
        dataset_version=dataset_version,
        defaults={"label": dataset_version.dataset.name},
    )

    # Dispatch background Celery task to build charts
    from apps.dashboards.tasks import build_dashboard_widgets
    task = build_dashboard_widgets.delay(str(dashboard.id), dataset_version.id, billing_profile.plan)
    dashboard.celery_task_id = task.id
    dashboard.save(update_fields=["celery_task_id"])

    # Redirect immediately – the browser will poll for build status
    return redirect("dashboard-detail", dashboard_id=dashboard.id)


def _build_widget_specs_from_ai(ai_specs: list, df, profile, column_roles: dict | None = None) -> list[dict]:
    """Convert AI-generated dashboard spec list into concrete widget specs with chart configs.

    Enhancements:
    - KPI widgets: humanized labels, proper icon metadata, trend computation, computed insights
    - Chart widgets: per-widget AI analysis if AI insight not already provided
    - Heading widgets: section separators preserved
    - Text canvas (narrative): preserved with is_narrative flag
    - All charts get ai_insight populated at build time
    """
    if column_roles is None:
        column_roles = {}

    specs = []
    position = 1
    for spec in ai_specs:
        chart_type = str(spec.get("chart_type", "bar")).lower()
        title = str(spec.get("title", "Widget")).strip() or "Widget"
        dimension = str(spec.get("dimension") or "").strip()
        measures = spec.get("measures") or []
        if isinstance(measures, str):
            measures = [measures]
        measures = [str(m).strip() for m in measures if str(m).strip()]
        measure = measures[0] if measures else ""
        x_measure = str(spec.get("x_measure") or "").strip()
        y_measure = str(spec.get("y_measure") or "").strip()
        palette = str(spec.get("palette") or "indigo").strip()
        if palette not in PALETTES:
            palette = "indigo"
        size = str(spec.get("size") or "md").strip()
        if size not in {"sm", "md", "lg"}:
            size = "md"
        ai_insight = str(spec.get("ai_insight") or "").strip()[:400]
        spec_agg = str(spec.get("_agg") or "").strip().lower()

        config: dict = {}

        # ── Structural/layout widgets ─────────────────────────────────────────
        if chart_type == "heading":
            heading_color = str(spec.get("_heading_color") or "indigo").strip()
            heading_font = str(spec.get("_heading_font_size") or "xl").strip()
            config = {
                "text": title,
                "color": heading_color,
                "font_size": heading_font,
                "align": "left",
                "font_family": "poppins",
                "layout": {"size": "lg"},
            }
            specs.append({"title": title, "widget_type": "heading", "position": position, "config": config})
            position += 1
            continue

        if chart_type == "text_canvas":
            narrative_content = str(spec.get("_narrative_content") or spec.get("content") or title).strip()
            is_narrative = bool(spec.get("_is_narrative"))
            config = {
                "content": narrative_content,
                "bg_color": "indigo",
                "text_size": "sm",
                "is_narrative": is_narrative,
                "layout": {"size": "lg"},
            }
            specs.append({"title": title, "widget_type": "text_canvas", "position": position, "config": config})
            position += 1
            continue

        # ── Chart/data widgets ─────────────────────────────────────────────────
        try:
            if chart_type == "kpi":
                # Resolve measure column with case-insensitive fallback
                resolved_measure = measure if measure and measure in df.columns else None
                if not resolved_measure and measure:
                    lower_map = {c.lower(): c for c in df.columns}
                    resolved_measure = lower_map.get(measure.lower())
                if resolved_measure:
                    role_info = column_roles.get(resolved_measure, {})
                    role_label = str(role_info.get("label") or "").strip()
                    human_label = role_label if role_label else _humanize_col(resolved_measure)
                    sem_type = str(role_info.get("data_type") or "").strip()
                    kpi_meta = _detect_kpi_meta(resolved_measure, semantic_type=sem_type)
                    role_agg = str(role_info.get("agg") or "sum").strip()
                    if sem_type == "percentage" and not spec_agg:
                        role_agg = "avg"
                    agg = spec_agg if spec_agg else role_agg

                    # Smart value formatting based on aggregation preference
                    if agg == "nunique":
                        display_val = f"{int(df[resolved_measure].nunique()):,}"
                        kpi_label = f"Unique {human_label}"
                    elif agg == "avg":
                        col_data = df[resolved_measure].dropna()
                        avg = col_data.mean()
                        display_val = f"{avg:,.2f}"
                        kpi_label = f"Avg {human_label}"
                    elif agg == "count":
                        col_data = df[resolved_measure].dropna()
                        display_val = f"{int(len(col_data)):,}"
                        kpi_label = f"{human_label} Count"
                    else:
                        col_data = df[resolved_measure].dropna()
                        total = col_data.sum()
                        avg = col_data.mean()
                        display_val = f"{total:,.0f}"
                        kpi_label = human_label
                    config = {
                        "kpi": kpi_label,
                        "value": display_val,
                        "kpi_meta": kpi_meta,
                        "layout": {"size": size},
                    }
                    if agg not in ("nunique", "count"):
                        trend = _compute_kpi_trend(df, resolved_measure)
                        if trend:
                            config["trend"] = trend
                    if not ai_insight:
                        try:
                            if agg == "nunique":
                                ai_insight = f"{kpi_label}: {display_val} distinct values across {profile.total_rows:,} records."
                            else:
                                col_data = df[resolved_measure].dropna()
                                avg = col_data.mean()
                                pct_above = round(sum(1 for v in col_data if v > avg) / len(col_data) * 100, 1)
                                ai_insight = (
                                    f"{kpi_label} totals {display_val} with a mean of {avg:,.2f}. "
                                    f"{pct_above}% of records exceed the average — "
                                    f"monitor this distribution for outlier-driven variance."
                                )
                        except Exception:
                            pass
                else:
                    config = {
                        "kpi": "Total Records",
                        "value": f"{profile.total_rows:,}",
                        "kpi_meta": {"icon": "people", "format": "count"},
                        "layout": {"size": size},
                    }
                    if not ai_insight:
                        ai_insight = f"This dashboard analyzes {profile.total_rows:,} records across {profile.total_columns} dimensions."

            elif chart_type == "bar" and dimension and measure and dimension in df.columns and measure in df.columns:
                top = df.groupby(dimension)[measure].sum().nlargest(10)
                labels = [str(l) for l in top.index]
                values = [round(float(v), 2) for v in top.values]
                config = _bar_config(labels, values, measure, palette)
                config["layout"] = {"size": size}
                if not ai_insight and labels and values:
                    try:
                        ai_insight, _ = ai_analyze_chart("bar", labels, values, title)
                    except Exception:
                        pass

            elif chart_type == "hbar" and dimension and measure and dimension in df.columns and measure in df.columns:
                top = df.groupby(dimension)[measure].sum().nlargest(10)
                labels = [str(l) for l in top.index]
                values = [round(float(v), 2) for v in top.values]
                config = _hbar_config(labels, values, measure, palette)
                config["layout"] = {"size": size}
                if not ai_insight and labels and values:
                    try:
                        ai_insight, _ = ai_analyze_chart("hbar", labels, values, title)
                    except Exception:
                        pass

            elif chart_type == "line" and dimension and measure and dimension in df.columns and measure in df.columns:
                tmp = df[[dimension, measure]].copy()
                try:
                    tmp[dimension] = _to_datetime_safely(tmp[dimension])
                    tmp = tmp.dropna(subset=[dimension])
                    trend_data = tmp.groupby(tmp[dimension].dt.to_period("M"))[measure].sum()
                except Exception:
                    trend_data = tmp.groupby(dimension)[measure].sum()
                labels = [str(p) for p in trend_data.index]
                values = [round(float(v), 2) for v in trend_data.values]
                config = _line_config(labels, values, measure, palette)
                config["layout"] = {"size": size}
                if not ai_insight and labels and values:
                    try:
                        ai_insight, _ = ai_analyze_chart("line", labels, values, title)
                    except Exception:
                        pass

            elif chart_type == "area" and dimension and measure and dimension in df.columns and measure in df.columns:
                tmp = df[[dimension, measure]].copy()
                try:
                    tmp[dimension] = _to_datetime_safely(tmp[dimension])
                    tmp = tmp.dropna(subset=[dimension])
                    trend_data = tmp.groupby(tmp[dimension].dt.to_period("M"))[measure].sum()
                except Exception:
                    trend_data = tmp.groupby(dimension)[measure].sum()
                labels = [str(p) for p in trend_data.index]
                values = [round(float(v), 2) for v in trend_data.values]
                config = _area_config(labels, values, measure, palette)
                config["layout"] = {"size": size}
                if not ai_insight and labels and values:
                    try:
                        ai_insight, _ = ai_analyze_chart("area", labels, values, title)
                    except Exception:
                        pass

            elif chart_type in ("pie", "doughnut") and dimension and dimension in df.columns:
                vc = (
                    df.groupby(dimension)[measure].sum().nlargest(6)
                    if measure and measure in df.columns
                    else df[dimension].value_counts().head(6)
                )
                labels = [str(l) for l in vc.index]
                values = [round(float(v), 2) for v in vc.values]
                fn = _pie_config if chart_type == "pie" else _doughnut_config
                config = fn(labels, values, palette)
                config["layout"] = {"size": size}
                if not ai_insight and labels and values:
                    try:
                        ai_insight, _ = ai_analyze_chart(chart_type, labels, values, title)
                    except Exception:
                        pass

            elif chart_type == "scatter" and x_measure and y_measure and x_measure in df.columns and y_measure in df.columns:
                tmp = df[[x_measure, y_measure]].dropna().head(500)
                x_vals = [round(float(v), 4) for v in tmp[x_measure]]
                y_vals = [round(float(v), 4) for v in tmp[y_measure]]
                config = _scatter_config(x_vals, y_vals, x_measure, y_measure, palette, f"{x_measure} vs {y_measure}")
                config["layout"] = {"size": size}
                if not ai_insight:
                    try:
                        ai_insight, _ = ai_analyze_chart("scatter", x_vals[:40], y_vals[:40], title)
                    except Exception:
                        pass

            elif chart_type == "radar" and dimension and measure and dimension in df.columns and measure in df.columns:
                top = df.groupby(dimension)[measure].sum().nlargest(8)
                labels = [str(l) for l in top.index]
                values = [round(float(v), 2) for v in top.values]
                config = _radar_config(labels, values, measure, palette)
                config["layout"] = {"size": size}
                if not ai_insight and labels and values:
                    try:
                        ai_insight, _ = ai_analyze_chart("radar", labels, values, title)
                    except Exception:
                        pass

            elif chart_type == "table":
                cols = [c for c in (([dimension] + measures) if dimension else measures) if c and c in df.columns]
                if not cols:
                    cols = [str(c) for c in df.columns[:6]]
                preview = df[cols].head(50).fillna("")
                rows = [[str(v) for v in row] for row in preview.values.tolist()]
                config = {"columns": cols, "rows": rows, "layout": {"size": size}}
                if not ai_insight:
                    ai_insight = (
                        f"Showing {len(rows)} records across {len(cols)} columns: "
                        f"{', '.join(_humanize_col(c) for c in cols[:3])}{'...' if len(cols) > 3 else ''}. "
                        f"Sort columns to rank performers and filter to focus on specific segments."
                    )

        except Exception:
            config = {}

        if not config:
            continue

        if ai_insight:
            config["ai_insight"] = ai_insight[:400]

        config["builder"] = {
            "dimension": dimension,
            "measures": measures,
            "measure": measure,
            "x_measure": x_measure,
            "y_measure": y_measure,
            "x_label": "",
            "y_label": "",
            "palette": palette,
            "tooltip_enabled": True,
            "table_columns": config.get("columns", []) if chart_type == "table" else [],
            "group_by": [],
            "dataset_version_id": None,
        }
        specs.append({
            "title": title,
            "widget_type": chart_type,
            "config": config,
            "position": position,
        })
        position += 1
    return specs


@login_required
def dashboard_create_share_link(request: HttpRequest, dashboard_id) -> HttpResponse:
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)
    DashboardShareLink.objects.create(dashboard=dashboard)

    referer = request.META.get("HTTP_REFERER", "")
    if f"/dashboards/{dashboard_id}/" in referer:
        return redirect("dashboard-detail", dashboard_id=dashboard_id)
    return redirect("app-home")


@login_required
def dashboard_build_status(request: HttpRequest, dashboard_id) -> JsonResponse:
    """Poll endpoint: returns build status + widget HTML once ready."""
    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)
    data: dict = {"status": dashboard.build_status}
    if dashboard.build_status == Dashboard.BuildStatus.READY:
        widgets = list(dashboard.widgets.order_by("position"))
        data["widget_count"] = len(widgets)
    return JsonResponse(data)


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


def _get_default_dataset_version(dashboard: Dashboard):
    """Return primary dataset_version, or first linked dataset version as fallback."""
    if dashboard.dataset_version_id:
        return dashboard.dataset_version
    first_link = dashboard.dataset_links.select_related("dataset_version").order_by("added_at").first()
    return first_link.dataset_version if first_link else None


@login_required
def dashboard_get_columns(request: HttpRequest, dashboard_id) -> JsonResponse:
    """Return column metadata for a dataset linked to a dashboard.

    Optional query param ``version_id`` selects which linked DatasetVersion to use.
    Falls back to the dashboard's primary dataset_version if not specified.
    """
    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)

    version_id = request.GET.get("version_id")
    if version_id:
        try:
            version_id = int(version_id)
        except (TypeError, ValueError):
            return JsonResponse({"error": "Invalid version_id"}, status=400)
        # Verify the version is actually linked to this dashboard
        linked_ids = list(
            dashboard.dataset_links.values_list("dataset_version_id", flat=True)
        )
        if dashboard.dataset_version_id:
            linked_ids.append(dashboard.dataset_version_id)
        if version_id not in linked_ids:
            return JsonResponse({"error": "Dataset not linked to this dashboard"}, status=403)
        dataset_version = get_object_or_404(DatasetVersion, id=version_id)
    else:
        dataset_version = _get_default_dataset_version(dashboard)

    if not dataset_version:
        return JsonResponse({"dimensions": [], "measures": [], "date_cols": [], "all_cols": [], "version_id": None})

    df = _load_df_from_version(dataset_version)
    if df is None:
        return JsonResponse({"dimensions": [], "measures": [], "date_cols": [], "all_cols": [], "version_id": dataset_version.id})

    profile = build_profile_summary(df)
    date_cols = [c for c in df.columns if any(k in str(c).lower() for k in ["date", "month", "year", "period", "quarter"])]

    # Include unique values per categorical column (for filter dropdowns), capped at 200 per col
    unique_values: dict = {}
    for col in profile.categorical_columns:
        vals = df[col].dropna().astype(str).unique().tolist()
        unique_values[col] = sorted(vals[:200])

    # Range info for numeric columns (for range sliders)
    range_info: dict = {}
    for col in profile.numeric_columns:
        try:
            mn = float(df[col].min())
            mx = float(df[col].max())
            if not (math.isfinite(mn) and math.isfinite(mx)):
                continue
            range_info[col] = {"min": round(mn, 4), "max": round(mx, 4)}
        except Exception:
            pass

    return JsonResponse({
        "dimensions": profile.categorical_columns,
        "measures": profile.numeric_columns,
        "date_cols": date_cols,
        "all_cols": [str(c) for c in df.columns],
        "version_id": dataset_version.id,
        "unique_values": unique_values,
        "range_info": range_info,
    })


@login_required
def dashboard_list_datasets(request: HttpRequest, dashboard_id) -> JsonResponse:
    """Return all DatasetVersions linked to a dashboard (for multi-dataset UI)."""
    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)

    datasets = []
    # Primary dataset_version (always first if set)
    primary_id = dashboard.dataset_version_id
    if primary_id:
        dv = dashboard.dataset_version
        datasets.append({
            "version_id": dv.id,
            "dataset_name": dv.dataset.name,
            "label": dv.dataset.name,
            "version": dv.version,
            "row_count": dv.row_count,
            "column_count": dv.column_count,
            "is_primary": True,
        })

    for link in dashboard.dataset_links.select_related("dataset_version__dataset").order_by("added_at"):
        if link.dataset_version_id == primary_id:
            continue  # already listed
        dv = link.dataset_version
        datasets.append({
            "version_id": dv.id,
            "dataset_name": dv.dataset.name,
            "label": link.label or dv.dataset.name,
            "version": dv.version,
            "row_count": dv.row_count,
            "column_count": dv.column_count,
            "is_primary": False,
        })

    return JsonResponse({"datasets": datasets})


@login_required
def dashboard_add_dataset(request: HttpRequest, dashboard_id) -> JsonResponse:
    """Link an existing DatasetVersion (from the same workspace) to a dashboard."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    version_id = data.get("version_id")
    label = str(data.get("label", "")).strip()[:100]

    if not version_id:
        return JsonResponse({"error": "version_id is required"}, status=400)

    dataset_version = get_object_or_404(
        DatasetVersion,
        id=version_id,
        dataset__workspace=dashboard.workspace,
    )

    link, created = DashboardDataset.objects.get_or_create(
        dashboard=dashboard,
        dataset_version=dataset_version,
        defaults={"label": label or dataset_version.dataset.name},
    )
    if not created and label:
        link.label = label
        link.save(update_fields=["label"])

    return JsonResponse({
        "success": True,
        "version_id": dataset_version.id,
        "dataset_name": dataset_version.dataset.name,
        "label": link.label,
        "row_count": dataset_version.row_count,
        "column_count": dataset_version.column_count,
    })


@login_required
def dashboard_remove_dataset(request: HttpRequest, dashboard_id, version_id: int) -> JsonResponse:
    """Unlink a DatasetVersion from a dashboard (cannot remove the primary dataset_version)."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)

    if dashboard.dataset_version_id == version_id:
        return JsonResponse({"error": "Cannot remove the primary dataset. Change the primary dataset first."}, status=400)

    deleted, _ = DashboardDataset.objects.filter(
        dashboard=dashboard, dataset_version_id=version_id
    ).delete()

    if not deleted:
        return JsonResponse({"error": "Dataset not linked to this dashboard"}, status=404)

    return JsonResponse({"success": True})


@login_required
def dashboard_add_widget(request: HttpRequest, dashboard_id) -> JsonResponse:
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
    widget = DashboardWidget.objects.create(
        dashboard=dashboard,
        source_dataset_version=result.get("dataset_version"),
        title=title,
        widget_type=chart_type,
        position=max_pos + 1,
        chart_config=config,
    )

    return JsonResponse({"success": True, "widget_id": widget.id, "chart_config": config})


@login_required
def dashboard_add_heading(request: HttpRequest, dashboard_id) -> JsonResponse:
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


def _resolve_dataset_version(dashboard: Dashboard, data: dict):
    """Return the DatasetVersion to use for a widget, based on data['dataset_version_id']."""
    version_id = data.get("dataset_version_id")
    if version_id:
        try:
            version_id = int(version_id)
        except (TypeError, ValueError):
            return None, "Invalid dataset_version_id"
        # Must be linked to this dashboard
        linked_ids = list(dashboard.dataset_links.values_list("dataset_version_id", flat=True))
        if dashboard.dataset_version_id:
            linked_ids.append(dashboard.dataset_version_id)
        if version_id not in linked_ids:
            return None, "Dataset not linked to this dashboard"
        dv = DatasetVersion.objects.filter(id=version_id).first()
        if not dv:
            return None, "Dataset version not found"
        return dv, None
    return _get_default_dataset_version(dashboard), None


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
    tooltip_enabled = data.get("tooltip_enabled", True)
    if not isinstance(tooltip_enabled, bool):
        tooltip_enabled = str(tooltip_enabled).lower() not in ("false", "0", "no")
    preview_only = bool(data.get("preview_only", False))
    # filters: list of {column, filter_type, value} to apply to df
    filters = data.get("filters", [])

    if chart_type not in _VALID_CHART_TYPES:
        return {"error": "Invalid chart type", "status": 400}

    # Pro/Plus gating – check caller's plan
    if chart_type in _PRO_CHART_TYPES:
        from apps.billing.models import UserProfile
        caller = getattr(dashboard.workspace, "owner", None)
        if caller:
            up, _ = UserProfile.objects.get_or_create(user=caller)
            if not up.is_pro:
                return {"error": "This chart type requires a Pro or Enterprise plan.", "status": 403}

    dataset_version, dv_error = _resolve_dataset_version(dashboard, data)
    if dv_error:
        return {"error": dv_error, "status": 400}

    config: dict = {}
    if chart_type == "kpi":
        if measure and dataset_version:
            df = _load_df_from_version(dataset_version)
            if df is not None:
                df = apply_df_filters(df, filters)
                if measure == "rows":
                    config = {
                        "kpi": "Total Records",
                        "value": f"{len(df):,}",
                        "kpi_meta": {"format": "count", "icon": "people"},
                    }
                elif measure in df.columns:
                    human_measure = _humanize_col(measure)
                    kpi_meta = _detect_kpi_meta(measure)
                    numeric_series = pd.to_numeric(df[measure], errors="coerce")
                    has_numeric = bool(numeric_series.notna().any())
                    if has_numeric:
                        total = float(numeric_series.fillna(0).sum())
                        value_display = f"{total:,.0f}"
                    else:
                        # Non-numeric columns should still yield a stable KPI instead of a server error.
                        value_display = f"{int(df[measure].astype(str).str.strip().ne('').sum()):,}"
                        kpi_meta = {"format": "count", "icon": "people"}
                    config = {
                        "kpi": human_measure,
                        "value": value_display,
                        "kpi_meta": kpi_meta,
                    }
                    if has_numeric:
                        trend = _compute_kpi_trend(df, measure)
                        if trend:
                            config["trend"] = trend
                else:
                    config = {
                        "kpi": _humanize_col(measure),
                        "value": "N/A",
                        "kpi_meta": _detect_kpi_meta(measure),
                    }
            else:
                config = {"kpi": _humanize_col(measure), "value": "N/A", "kpi_meta": _detect_kpi_meta(measure)}
        else:
            config = {"kpi": "Value", "value": "0", "kpi_meta": {"format": "number", "icon": "chart"}}
    else:
        if not dataset_version:
            return {"error": "Dashboard has no dataset", "status": 400}
        df = _load_df_from_version(dataset_version)
        if df is None:
            return {"error": "Could not load dataset file", "status": 500}
        df = apply_df_filters(df, filters)
        if chart_type == "smart":
            ai_prompt = str(data.get("ai_prompt", "")).strip() or title
            rec = _ai_smart_chart(df, ai_prompt)
            chart_type = rec.get("chart_type", "bar")
            title = rec.get("title", title)
            if rec.get("dimension"):
                dimension = rec["dimension"]
            if rec.get("measures"):
                measures = rec["measures"]
                measure = measures[0]
            if rec.get("x_measure"):
                x_measure = rec["x_measure"]
            if rec.get("y_measure"):
                y_measure = rec["y_measure"]
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
                        tmp[dimension] = _to_datetime_safely(tmp[dimension])
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
                            tmp[dimension] = _to_datetime_safely(tmp[dimension])
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
                    tmp[dimension] = _to_datetime_safely(tmp[dimension])
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
            elif chart_type in {"scatter", "map"}:
                if not x_measure or not y_measure:
                    return {"error": "x_measure and y_measure are required for scatter/map charts", "status": 400}
                if x_measure not in df.columns or y_measure not in df.columns:
                    return {"error": "Selected columns not found in dataset", "status": 400}
                tmp = df[[x_measure, y_measure]].dropna().head(500)
                subtitle = f"{x_measure} vs {y_measure}" if chart_type == "scatter" else f"Map points: {x_measure} / {y_measure}"
                config = _scatter_config(
                    [round(float(v), 4) for v in tmp[x_measure].tolist()],
                    [round(float(v), 4) for v in tmp[y_measure].tolist()],
                    x_measure,
                    y_measure,
                    palette,
                    subtitle,
                )
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
            # ── Pro chart types ──────────────────────────────────────────────────
            elif chart_type == "bubble":
                if not x_measure or not y_measure:
                    return {"error": "x_measure and y_measure are required for bubble charts", "status": 400}
                r_col = measures[1] if len(measures) > 1 else None
                tmp = df[[c for c in [x_measure, y_measure, r_col] if c and c in df.columns]].dropna().head(300)
                if r_col and r_col in tmp.columns:
                    r_vals = tmp[r_col].tolist()
                    r_min, r_max = min(r_vals), max(r_vals)
                    r_range = (r_max - r_min) or 1
                    pts = [{"x": round(float(x), 4), "y": round(float(y), 4), "r": round(3 + 20 * (r - r_min) / r_range, 2)}
                           for x, y, r in zip(tmp[x_measure], tmp[y_measure], tmp[r_col])]
                else:
                    pts = [{"x": round(float(x), 4), "y": round(float(y), 4), "r": 6}
                           for x, y in zip(tmp[x_measure], tmp[y_measure])]
                config = _bubble_config(pts, title, palette, x_measure, y_measure)
            elif chart_type == "polararea":
                if not dimension:
                    return {"error": "dimension is required for polar area charts", "status": 400}
                vc = df.groupby(dimension)[measure].sum().nlargest(8) if measure and measure in df.columns else df[dimension].value_counts().head(8)
                config = _polararea_config([str(l) for l in vc.index.tolist()], [round(float(v), 2) for v in vc.values.tolist()], palette)
            elif chart_type == "mixed":
                if not dimension or not measures:
                    return {"error": "dimension and measures are required for mixed charts", "status": 400}
                all_cats = [str(c) for c in df[dimension].dropna().unique()[:15]]
                bar_ds, line_ds = [], []
                for i, m in enumerate(measures):
                    if m not in df.columns:
                        continue
                    grp = df.groupby(dimension)[m].sum()
                    vals = [round(float(grp.get(c, 0)), 2) for c in all_cats]
                    if i == len(measures) - 1 and len(measures) > 1:
                        line_ds.append({"label": m, "data": vals})
                    else:
                        bar_ds.append({"label": m, "data": vals})
                config = _mixed_config(all_cats, bar_ds, line_ds, palette, x_label, y_label)
            elif chart_type == "funnel":
                if not dimension or not measure:
                    return {"error": "dimension and measure are required for funnel charts", "status": 400}
                top = df.groupby(dimension)[measure].sum().nlargest(10)
                config = _funnel_config([str(l) for l in top.index.tolist()], [round(float(v), 2) for v in top.values.tolist()], measure, palette)
            elif chart_type == "gauge":
                if not measure:
                    return {"error": "measure is required for gauge charts", "status": 400}
                if measure not in df.columns:
                    return {"error": "Selected measure not found in dataset", "status": 400}
                val = float(df[measure].sum())
                max_val = float(df[measure].max()) * len(df) if float(df[measure].max()) > 0 else val * 2
                config = _gauge_config(val, 0, max_val or 1, measure, palette)
            elif chart_type == "waterfall":
                if not dimension or not measure:
                    return {"error": "dimension and measure are required for waterfall charts", "status": 400}
                top = df.groupby(dimension)[measure].sum().head(12)
                config = _waterfall_config([str(l) for l in top.index.tolist()], [round(float(v), 2) for v in top.values.tolist()], measure, palette, x_label, y_label)
        except Exception as exc:
            return {"error": str(exc), "status": 500}
    if isinstance(config, dict):
        # Apply tooltip visibility
        if not tooltip_enabled:
            opts = config.setdefault("options", {})
            plugins = opts.setdefault("plugins", {})
            plugins["tooltip"] = {"enabled": False}
        config["builder"] = {
            "dimension": dimension,
            "measures": measures,
            "measure": measure,
            "x_measure": x_measure,
            "y_measure": y_measure,
            "x_label": x_label,
            "y_label": y_label,
            "palette": palette,
            "tooltip_enabled": tooltip_enabled,
            "table_columns": table_columns,
            "group_by": group_by,
            "dataset_version_id": dataset_version.id if dataset_version else None,
        }
    return {
        "chart_type": chart_type,
        "title": title,
        "config": config,
        "preview_only": preview_only,
        "dataset_version": dataset_version,
    }


@login_required
def dashboard_update_widget(request: HttpRequest, dashboard_id, widget_id) -> JsonResponse:
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
    widget.source_dataset_version = result.get("dataset_version")
    widget.save(update_fields=["title", "widget_type", "chart_config", "source_dataset_version"])
    return JsonResponse({"success": True, "widget_id": widget.id, "chart_config": widget.chart_config})


@login_required
def dashboard_resize_widget(request: HttpRequest, dashboard_id, widget_id) -> JsonResponse:
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
        if height < 100 or height > 1200:
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
def dashboard_rename(request: HttpRequest, dashboard_id) -> JsonResponse:
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
def dashboard_delete_widget(request: HttpRequest, dashboard_id, widget_id) -> JsonResponse:
    """Delete a single widget from a dashboard."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)
    widget = get_object_or_404(DashboardWidget, id=widget_id, dashboard=dashboard)
    widget.delete()

    return JsonResponse({"success": True})


@login_required
def dashboard_rename_widget(request: HttpRequest, dashboard_id, widget_id) -> JsonResponse:
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


@login_required
def dashboard_reorder_widgets(request: HttpRequest, dashboard_id) -> JsonResponse:
    """Reorder widgets by accepting an ordered list of widget IDs."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    ordered_ids = data.get("order", [])
    if not isinstance(ordered_ids, list):
        return JsonResponse({"error": "order must be a list of widget IDs"}, status=400)
    widget_map = {w.id: w for w in dashboard.widgets.all()}
    for pos, wid in enumerate(ordered_ids, start=1):
        try:
            wid = int(wid)
        except (TypeError, ValueError):
            continue
        if wid in widget_map:
            w = widget_map[wid]
            w.position = pos
            w.save(update_fields=["position"])
    return JsonResponse({"success": True})


@login_required
def dashboard_add_text_canvas(request: HttpRequest, dashboard_id) -> JsonResponse:
    """Create a freeform text canvas widget."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    content = str(data.get("content", "")).strip()
    if not content:
        return JsonResponse({"error": "Content is required"}, status=400)
    if len(content) > 4000:
        return JsonResponse({"error": "Content too long (max 4000 chars)"}, status=400)

    title = str(data.get("title", "Text Block")).strip()[:200] or "Text Block"
    bg_color = str(data.get("bg_color", "white")).strip().lower()
    if bg_color not in {"white", "slate", "indigo", "emerald", "rose", "amber", "yellow"}:
        bg_color = "white"
    text_size = str(data.get("text_size", "sm")).strip().lower()
    if text_size not in {"xs", "sm", "base", "lg"}:
        text_size = "sm"

    max_pos = dashboard.widgets.order_by("-position").values_list("position", flat=True).first() or 0
    config = {
        "content": content,
        "bg_color": bg_color,
        "text_size": text_size,
        "layout": {"size": "lg"},
    }
    widget = DashboardWidget.objects.create(
        dashboard=dashboard,
        title=title,
        widget_type="text_canvas",
        position=max_pos + 1,
        chart_config=config,
    )
    return JsonResponse({"success": True, "widget_id": widget.id})


@login_required
def dashboard_update_heading(request: HttpRequest, dashboard_id, widget_id) -> JsonResponse:
    """Update an existing heading widget's text and styling."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)
    widget = get_object_or_404(DashboardWidget, id=widget_id, dashboard=dashboard)
    if widget.widget_type != "heading":
        return JsonResponse({"error": "Widget is not a heading"}, status=400)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    text = str(data.get("text", "")).strip()
    if not text:
        return JsonResponse({"error": "Heading text is required"}, status=400)
    if len(text) > 200:
        return JsonResponse({"error": "Heading text too long (max 200 chars)"}, status=400)

    font_size = str(data.get("font_size", widget.chart_config.get("font_size", "2xl"))).strip().lower()
    if font_size not in {"lg", "xl", "2xl", "3xl"}:
        font_size = "2xl"
    color = str(data.get("color", widget.chart_config.get("color", "indigo"))).strip().lower()
    if color not in {"slate", "indigo", "emerald", "rose", "amber"}:
        color = "indigo"
    font_family = str(data.get("font_family", widget.chart_config.get("font_family", "inter"))).strip().lower()
    if font_family not in {"inter", "poppins", "serif", "mono"}:
        font_family = "inter"
    align = str(data.get("align", widget.chart_config.get("align", "left"))).strip().lower()
    if align not in {"left", "center", "right"}:
        align = "left"

    widget.chart_config = {
        **widget.chart_config,
        "text": text,
        "font_size": font_size,
        "color": color,
        "font_family": font_family,
        "align": align,
    }
    widget.title = text[:80]
    widget.save(update_fields=["chart_config", "title"])
    return JsonResponse({"success": True})


@login_required
def dashboard_update_text_canvas(request: HttpRequest, dashboard_id, widget_id) -> JsonResponse:
    """Update an existing text canvas widget's content and styling."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)
    widget = get_object_or_404(DashboardWidget, id=widget_id, dashboard=dashboard)
    if widget.widget_type != "text_canvas":
        return JsonResponse({"error": "Widget is not a text canvas"}, status=400)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    content = str(data.get("content", "")).strip()
    if not content:
        return JsonResponse({"error": "Content is required"}, status=400)
    if len(content) > 4000:
        return JsonResponse({"error": "Content too long (max 4000 chars)"}, status=400)

    title = str(data.get("title", widget.title)).strip()[:200] or widget.title
    bg_color = str(data.get("bg_color", widget.chart_config.get("bg_color", "white"))).strip().lower()
    if bg_color not in {"white", "slate", "indigo", "emerald", "rose", "amber", "yellow"}:
        bg_color = "white"
    text_size = str(data.get("text_size", widget.chart_config.get("text_size", "sm"))).strip().lower()
    if text_size not in {"xs", "sm", "base", "lg"}:
        text_size = "sm"

    widget.chart_config = {
        **widget.chart_config,
        "content": content,
        "bg_color": bg_color,
        "text_size": text_size,
    }
    widget.title = title
    widget.save(update_fields=["chart_config", "title"])
    return JsonResponse({"success": True})


@login_required
def dashboard_add_divider(request: HttpRequest, dashboard_id) -> JsonResponse:
    """Create a visual divider/separator widget between sections."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    label = str(data.get("label", "")).strip()[:100]
    after_widget_id = data.get("after_widget_id")
    insert_pos = 1
    if after_widget_id not in (None, "", 0, "0"):
        try:
            after_id = int(after_widget_id)
        except (TypeError, ValueError):
            return JsonResponse({"error": "Invalid after_widget_id"}, status=400)
        after_widget = get_object_or_404(DashboardWidget, id=after_id, dashboard=dashboard)
        insert_pos = after_widget.position + 1
    dashboard.widgets.filter(position__gte=insert_pos).update(position=models.F("position") + 1)
    config = {"label": label, "layout": {"size": "lg"}}
    widget = DashboardWidget.objects.create(
        dashboard=dashboard,
        title=label or "—",
        widget_type="divider",
        position=insert_pos,
        chart_config=config,
    )
    return JsonResponse({"success": True, "widget_id": widget.id})


@login_required
def dashboard_update_widget_span(request: HttpRequest, dashboard_id, widget_id) -> JsonResponse:
    """Update only the layout.size (column span) of a widget without page reload."""
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
    cfg = widget.chart_config or {}
    layout = cfg.get("layout", {})
    layout["size"] = size
    cfg["layout"] = layout
    widget.chart_config = cfg
    widget.save(update_fields=["chart_config"])
    return JsonResponse({"success": True, "size": size})


@login_required
def dashboard_save_filters(request: HttpRequest, dashboard_id) -> JsonResponse:
    """Persist the filter configuration for this dashboard."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    raw_filters = data.get("filters", [])
    if not isinstance(raw_filters, list):
        return JsonResponse({"error": "filters must be a list"}, status=400)

    # Validate and sanitise each filter entry
    valid_types = {"dropdown", "radio", "multiselect", "range"}
    categorical_types = {"dropdown", "radio", "multiselect"}

    known_cols: set[str] = set()
    numeric_cols: set[str] = set()
    categorical_cols: set[str] = set()
    dataset_version = _get_default_dataset_version(dashboard)
    if dataset_version:
        df = _load_df_from_version(dataset_version)
        if df is not None:
            known_cols = set(df.columns)
            profile = build_profile_summary(df)
            numeric_cols = set(profile.numeric_columns)
            categorical_cols = set(profile.categorical_columns)

    clean_filters = []
    for f in raw_filters:
        col = str(f.get("column", "")).strip()
        ftype = str(f.get("filter_type", "dropdown")).strip()
        if not col:
            continue
        if known_cols and col not in known_cols:
            # Ignore stale filters for columns that no longer exist.
            continue
        if ftype not in valid_types:
            ftype = "dropdown"

        # Enforce a compatible filter type based on actual column dtype.
        if col in numeric_cols:
            ftype = "range"
        elif col in categorical_cols and ftype == "range":
            ftype = "dropdown"
        elif col not in numeric_cols and col not in categorical_cols and ftype not in categorical_types:
            # Unknown column category (e.g., datetime): default to a categorical-style control.
            ftype = "dropdown"

        clean_filters.append({
            "id": str(f.get("id", col)),
            "column": col,
            "filter_type": ftype,
            "label": str(f.get("label", col)).strip()[:80],
            "version_id": f.get("version_id"),
        })

    dashboard.filter_config = clean_filters
    dashboard.save(update_fields=["filter_config"])
    return JsonResponse({"success": True, "filters": clean_filters})


@login_required
def dashboard_apply_filters(request: HttpRequest, dashboard_id) -> JsonResponse:
    """Return updated chart configs for all chart-type widgets with filters applied.

    POST body: { "filters": [{column, filter_type, value}, ...] }
    Returns: { "widgets": { "<widget_id>": <chart_config>, ... } }
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    filters = data.get("filters", [])
    if not isinstance(filters, list):
        filters = []

    _CHART_WIDGET_TYPES = {
        "bar", "line", "area", "hbar", "pie", "doughnut", "scatter", "map",
        "radar", "bubble", "polararea", "mixed", "funnel", "gauge", "waterfall", "kpi",
        "table",
    }

    widgets = dashboard.widgets.order_by("position")
    result: dict = {}

    for widget in widgets:
        if widget.widget_type not in _CHART_WIDGET_TYPES:
            continue
        builder = (widget.chart_config or {}).get("builder")
        if not builder:
            continue

        # Build a synthetic request dict for _build_widget_config
        rebuild_data = {
            "chart_type": widget.widget_type,
            "title": widget.title,
            "dimension": builder.get("dimension", ""),
            "measures": builder.get("measures", []),
            "measure": builder.get("measure", ""),
            "x_measure": builder.get("x_measure", ""),
            "y_measure": builder.get("y_measure", ""),
            "x_label": builder.get("x_label", ""),
            "y_label": builder.get("y_label", ""),
            "table_columns": builder.get("table_columns", []),
            "group_by": builder.get("group_by", []),
            "palette": builder.get("palette", "indigo"),
            "tooltip_enabled": builder.get("tooltip_enabled", True),
            "preview_only": True,
            "filters": filters,
        }
        # Use the widget's source dataset version if available, else dashboard primary
        if widget.source_dataset_version_id:
            rebuild_data["dataset_version_id"] = widget.source_dataset_version_id
        elif builder.get("dataset_version_id"):
            rebuild_data["dataset_version_id"] = builder["dataset_version_id"]

        rebuilt = _build_widget_config(dashboard, rebuild_data)
        if not rebuilt.get("error"):
            result[str(widget.id)] = rebuilt.get("config", {})

    return JsonResponse({"success": True, "widgets": result})


@login_required
def dashboard_get_filter_columns(request: HttpRequest, dashboard_id) -> JsonResponse:
    """Return columns available for filtering (dimension columns with their unique values)."""
    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)
    dataset_version = _get_default_dataset_version(dashboard)
    if not dataset_version:
        return JsonResponse({"columns": []})

    df = _load_df_from_version(dataset_version)
    if df is None:
        return JsonResponse({"columns": []})

    profile = build_profile_summary(df)
    columns = []

    for col in profile.categorical_columns[:30]:
        vals = df[col].dropna().astype(str).unique().tolist()
        columns.append({
            "column": col,
            "type": "categorical",
            "unique_values": sorted(vals[:200]),
        })

    for col in profile.numeric_columns[:20]:
        try:
            mn = float(df[col].min())
            mx = float(df[col].max())
            if not (math.isfinite(mn) and math.isfinite(mx)):
                continue
            columns.append({
                "column": col,
                "type": "numeric",
                "min": round(mn, 4),
                "max": round(mx, 4),
            })
        except Exception:
            pass

    return JsonResponse({"columns": columns, "filter_config": dashboard.filter_config or []})


@login_required
def dashboard_ai_analyze_widget(request: HttpRequest, dashboard_id, widget_id) -> JsonResponse:
    """Return AI-generated analysis text for a specific widget's chart data."""
    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)
    widget = get_object_or_404(DashboardWidget, id=widget_id, dashboard=dashboard)

    chart_config = widget.chart_config or {}
    chart_type = widget.widget_type
    title = widget.title

    # Extract labels and values from the Chart.js config
    labels: list = []
    values: list = []
    try:
        data = chart_config.get("data", {})
        labels = data.get("labels", [])
        datasets = data.get("datasets", [])
        if datasets:
            values = datasets[0].get("data", [])
    except Exception:
        pass

    # For KPI widgets
    if chart_type == "kpi":
        kpi_value = chart_config.get("value", "N/A")
        kpi_col = chart_config.get("kpi", "")
        analysis = f"KPI '{kpi_col}': {kpi_value}. This is a key metric summarised across the entire dataset."
        return JsonResponse({"success": True, "analysis": analysis, "ai_powered": False})

    analysis, ai_powered = ai_analyze_chart(chart_type, labels, values, title)
    return JsonResponse({"success": True, "analysis": analysis, "ai_powered": ai_powered})


@login_required
def dashboard_ai_suggest_slicers(request: HttpRequest, dashboard_id) -> JsonResponse:
    """Return AI-suggested slicers for this dashboard's primary dataset."""
    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)
    dataset_version = _get_default_dataset_version(dashboard)
    if not dataset_version:
        return JsonResponse({"error": "No dataset linked to this dashboard"}, status=400)

    df = _load_df_from_version(dataset_version)
    if df is None:
        return JsonResponse({"error": "Could not load dataset file"}, status=500)

    profile = build_profile_summary(df)
    suggestions, ai_powered = ai_suggest_slicers(df, profile)
    return JsonResponse({"success": True, "suggestions": suggestions, "ai_powered": ai_powered})


@login_required
def dashboard_ai_clean_dataset(request: HttpRequest, dashboard_id) -> JsonResponse:
    """Run AI data cleaning on the primary dataset and return a cleaning report."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)
    dataset_version = _get_default_dataset_version(dashboard)
    if not dataset_version:
        return JsonResponse({"error": "No dataset linked to this dashboard"}, status=400)

    df = _load_df_from_version(dataset_version)
    if df is None:
        return JsonResponse({"error": "Could not load dataset file"}, status=500)

    _, report = ai_clean_dataframe(df)
    from django.conf import settings
    report["ai_powered"] = bool(getattr(settings, "OPENAI_API_KEY", ""))
    return JsonResponse({"success": True, "report": report})


@login_required
def dashboard_ai_executive_summary(request: HttpRequest, dashboard_id) -> JsonResponse:
    """Generate an AI-powered executive summary for the dashboard (used for PDF export)."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)
    dataset_version = _get_default_dataset_version(dashboard)
    if not dataset_version:
        return JsonResponse({"error": "No dataset linked to this dashboard"}, status=400)

    df = _load_df_from_version(dataset_version)
    if df is None:
        return JsonResponse({"error": "Could not load dataset file"}, status=500)

    profile = build_profile_summary(df)
    widgets = dashboard.widgets.order_by("position")
    widget_titles = [w.title for w in widgets]

    summary = ai_generate_executive_summary(
        df=df,
        profile=profile,
        dashboard_title=dashboard.title,
        widget_titles=widget_titles,
    )
    return JsonResponse({"success": True, "summary": summary})


@login_required
def dashboard_ai_enhance_presentation_text(request: HttpRequest, dashboard_id) -> JsonResponse:
    """Enhance presentation slide text using AI (or deterministic fallback)."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        payload = {}
    raw_text = str(payload.get("text", "")).strip()
    if not raw_text:
        return JsonResponse({"error": "Text is required"}, status=400)

    client, model = _get_ai_client()
    if client is not None:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a presentation writing assistant. Rewrite text to be concise, persuasive, "
                            "and executive-friendly. Keep facts unchanged. Return JSON only: {\"enhanced_text\":\"...\"}."
                        ),
                    },
                    {"role": "user", "content": raw_text},
                ],
                temperature=0.2,
                timeout=10,
            )
            content = ((response.choices[0].message.content) or "").strip()
            match = re.search(r"\{.*\}", content, flags=re.DOTALL)
            parsed = json.loads(match.group(0) if match else content)
            enhanced = str(parsed.get("enhanced_text", "")).strip()
            if enhanced:
                return JsonResponse({"success": True, "enhanced_text": enhanced, "ai_powered": True})
        except Exception:
            pass

    # Fallback: deterministic readability cleanup.
    enhanced = " ".join(raw_text.split())
    if len(enhanced) > 0:
        enhanced = enhanced[0].upper() + enhanced[1:]
    if not enhanced.endswith("."):
        enhanced += "."
    return JsonResponse({"success": True, "enhanced_text": enhanced, "ai_powered": False})


@login_required
def dashboard_ai_generate_html(request: HttpRequest, dashboard_id) -> JsonResponse:
    """Generate a complete standalone HTML dashboard using OpenAI.

    POST /dashboards/<uuid>/ai/generate-html/
    Returns JSON: {"success": true, "html": "<full html string>"}
    The client can open the HTML in a new tab or offer it as a download.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    dashboard = get_object_or_404(Dashboard, id=dashboard_id, workspace__owner=request.user)
    dataset_version = _get_default_dataset_version(dashboard)
    if not dataset_version:
        return JsonResponse({"error": "No dataset linked to this dashboard"}, status=400)

    df = _load_df_from_version(dataset_version)
    if df is None:
        return JsonResponse({"error": "Could not load dataset file"}, status=500)

    profile = build_profile_summary(df)
    html = ai_generate_html_dashboard(df=df, profile=profile, dataset_name=dashboard.title)

    if html is None:
        return JsonResponse(
            {"error": "HTML generation unavailable. Ensure OPENAI_API_KEY is set."},
            status=503,
        )

    return JsonResponse({"success": True, "html": html})
