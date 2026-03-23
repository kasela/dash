"""Celery tasks for background dashboard/chart generation."""
import logging
from pathlib import Path

from celery import shared_task

logger = logging.getLogger(__name__)


def _sanitize_for_json(value):
    """Recursively convert values to JSON-safe primitives for JSONField storage.

    Prevents sqlite JSON_VALID failures from NaN/Infinity and non-serializable scalars.
    """
    import math

    if isinstance(value, dict):
        return {str(k): _sanitize_for_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_for_json(v) for v in value]
    if isinstance(value, tuple):
        return [_sanitize_for_json(v) for v in value]
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    # numpy/pandas scalar support
    if hasattr(value, "item") and callable(getattr(value, "item")):
        try:
            return _sanitize_for_json(value.item())
        except Exception:
            return str(value)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)


@shared_task(bind=True, max_retries=2, default_retry_delay=5)
def build_dashboard_widgets(self, dashboard_id: str, version_id: int, plan: str = "free"):
    """Background task: generate all widgets for a newly created dashboard.

    Runs after the dashboard record is created so the user can be redirected
    immediately. Sets dashboard.build_status → 'building' then 'ready'/'failed'.

    Full pipeline:
    1. Load DataFrame + build profile
    2. AI: detect column roles (measure/dimension/date/id)
    3. AI: generate executive dashboard title
    4. AI: suggest slicer/filter columns
    5. AI: design comprehensive dashboard spec (KPIs, charts, tables, narrative, headings)
    6. Build concrete widget configs from specs + enrich with per-widget AI insights
    7. AI: generate comprehensive dashboard narrative
    8. Save all widgets to DB (narrative → position 0, then all others)
    """
    from apps.dashboards.models import Dashboard, DashboardWidget
    from apps.datasets.models import DatasetVersion
    from apps.datasets.services import (
        build_profile_summary,
        deduplicate_chart_specs,
        ai_generate_dashboard_specs,
        ai_generate_dashboard_title,
        ai_suggest_slicers,
        ai_detect_column_roles,
        ai_generate_comprehensive_insights,
        generate_widget_specs_from_version,
        _humanize_col,
    )

    try:
        dashboard = Dashboard.objects.get(id=dashboard_id)
        dataset_version = DatasetVersion.objects.get(id=version_id)
    except Exception as exc:
        logger.error("build_dashboard_widgets: object not found – %s", exc)
        return

    dashboard.build_status = Dashboard.BuildStatus.BUILDING
    dashboard.save(update_fields=["build_status"])

    try:
        df = _load_df(dataset_version)

        if df is not None:
            profile = build_profile_summary(df)

            # Step 1: AI column role detection
            logger.info("Dashboard %s: detecting column roles via AI", dashboard_id)
            try:
                column_roles = ai_detect_column_roles(df, profile)
                logger.info("Column roles detected: %d columns classified", len(column_roles))
            except Exception as exc:
                logger.warning("Column role detection failed: %s", exc)
                column_roles = {}

            # Step 2: AI-powered title
            logger.info("Dashboard %s: generating title", dashboard_id)
            ai_title = ai_generate_dashboard_title(df, profile, dataset_version.dataset.name)
            if ai_title:
                dashboard.title = ai_title
                dashboard.save(update_fields=["title"])

            # Step 3: AI slicer suggestions
            logger.info("Dashboard %s: generating slicer suggestions", dashboard_id)
            slicer_suggestions, _ = ai_suggest_slicers(df, profile)
            if slicer_suggestions:
                auto_filters = []
                for s in slicer_suggestions:
                    col = s["column"]
                    raw_label = str(s.get("label") or "").strip()
                    if not raw_label or raw_label == col or "_" in raw_label or raw_label.islower():
                        label = _humanize_col(col)
                    else:
                        label = raw_label
                    auto_filters.append({
                        "id": col,
                        "column": col,
                        "filter_type": s["filter_type"],
                        "label": label,
                        "version_id": None,
                    })
                dashboard.filter_config = auto_filters
                dashboard.save(update_fields=["filter_config"])

            # Step 4: Comprehensive dashboard spec
            logger.info(
                "Dashboard %s: generating comprehensive dashboard specs (plan=%s)",
                dashboard_id,
                plan,
            )
            ai_specs = ai_generate_dashboard_specs(
                df,
                profile,
                dataset_version.dataset.name,
                plan=plan,
                column_roles=column_roles,
            )

            if ai_specs:
                logger.info("AI specs generated: %d widgets planned", len(ai_specs))
                widget_specs = _build_widget_specs_from_ai(ai_specs, df, profile, column_roles)
                widget_specs = deduplicate_chart_specs(widget_specs)
                logger.info("After deduplication: %d widgets retained", len(widget_specs))
            else:
                logger.info("AI specs unavailable; using heuristic generator")
                widget_specs = generate_widget_specs_from_version(dataset_version)

            # Step 5: Comprehensive AI narrative widget
            logger.info("Dashboard %s: generating comprehensive insights narrative", dashboard_id)
            try:
                widget_titles = [s.get("title", "") for s in widget_specs if s.get("title")]
                narrative_data = ai_generate_comprehensive_insights(
                    df,
                    profile,
                    dashboard.title,
                    widget_titles,
                )
                narrative_widget = _build_narrative_widget(narrative_data, dashboard.title)
                if narrative_widget:
                    widget_specs = [narrative_widget] + widget_specs
                    for i, s in enumerate(widget_specs):
                        s["position"] = i + 1
            except Exception as exc:
                logger.warning("Narrative generation failed: %s", exc)

        else:
            widget_specs = generate_widget_specs_from_version(dataset_version)

        # Save widgets to DB
        if widget_specs:
            for spec in widget_specs:
                safe_config = _sanitize_for_json(spec.get("config", {}))
                DashboardWidget.objects.create(
                    dashboard=dashboard,
                    title=spec["title"],
                    widget_type=spec["widget_type"],
                    position=spec["position"],
                    chart_config=safe_config,
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
                chart_config={
                    "type": "bar",
                    "data": {
                        "labels": ["A", "B", "C", "D"],
                        "datasets": [{
                            "label": "Count",
                            "data": [40, 30, 20, 10],
                            "backgroundColor": ["#6366f1", "#8b5cf6", "#a78bfa", "#c4b5fd"],
                            "borderRadius": 6,
                        }],
                    },
                    "options": {
                        "responsive": True,
                        "maintainAspectRatio": False,
                        "plugins": {"legend": {"display": False}},
                    },
                },
            )

        dashboard.build_status = Dashboard.BuildStatus.READY
        dashboard.save(update_fields=["build_status"])
        logger.info(
            "Dashboard %s built successfully with %d widgets",
            dashboard_id,
            len(widget_specs or []),
        )

    except Exception as exc:
        logger.exception("build_dashboard_widgets failed for %s: %s", dashboard_id, exc)
        try:
            dashboard.build_status = Dashboard.BuildStatus.FAILED
            dashboard.save(update_fields=["build_status"])
        except Exception:
            pass
        raise self.retry(exc=exc)


# Helpers


def _load_df(dataset_version):
    """Load DataFrame from a DatasetVersion's source file."""
    import pandas as pd
    from apps.datasets.services import detect_and_clean_headers

    try:
        file_path = dataset_version.source_file.path
        name = Path(file_path).name.lower()

        if name.endswith(".csv"):
            df = pd.read_csv(file_path)
        elif name.endswith((".xlsx", ".xlsm", ".xls")):
            df = pd.read_excel(file_path)
        elif name.endswith(".json"):
            df = pd.read_json(file_path)
        else:
            return None

        return detect_and_clean_headers(df)
    except Exception as exc:
        logger.warning("Failed to load dataframe from dataset version %s: %s", dataset_version.id, exc)
        return None


def _build_narrative_widget(narrative_data: dict, dashboard_title: str) -> dict | None:
    """Build a special AI Narrative text_canvas widget from comprehensive insights data."""
    if not narrative_data:
        return None

    executive_summary = str(narrative_data.get("executive_summary") or "").strip()
    if not executive_summary:
        return None

    key_findings = [str(f).strip() for f in (narrative_data.get("key_findings") or []) if str(f).strip()]
    strategic_recs = [str(r).strip() for r in (narrative_data.get("strategic_recs") or []) if str(r).strip()]
    data_health = str(narrative_data.get("data_health") or "").strip()
    analyst_note = str(narrative_data.get("analyst_note") or "").strip()

    config = {
        "content": executive_summary,
        "bg_color": "indigo",
        "text_size": "sm",
        "is_narrative": True,
        "narrative_data": {
            "executive_summary": executive_summary,
            "key_findings": key_findings,
            "strategic_recs": strategic_recs,
            "data_health": data_health,
            "analyst_note": analyst_note,
        },
        "layout": {"size": "lg"},
    }

    return {
        "title": f"AI Analysis: {dashboard_title}",
        "widget_type": "text_canvas",
        "position": 1,
        "config": config,
    }


def _build_widget_specs_from_ai(ai_specs: list, df, profile, column_roles: dict | None = None) -> list[dict]:
    """Convert AI-generated dashboard spec list into concrete widget specs."""
    import pandas as pd
    from apps.datasets.services import (
        PALETTES,
        _area_config,
        _bar_config,
        _doughnut_config,
        _hbar_config,
        _line_config,
        _pie_config,
        _radar_config,
        _humanize_col,
        _detect_kpi_meta,
        _compute_kpi_trend,
        ai_analyze_chart,
    )

    if column_roles is None:
        column_roles = {}

    _lower_map: dict[str, str] = {str(c).lower(): c for c in df.columns}
    _strip_map: dict[str, str] = {
        str(c).lower().replace("_", "").replace(" ", ""): c for c in df.columns
    }

    def _resolve_col(name: str) -> str | None:
        if not name:
            return None
        if name in df.columns:
            return name
        lower = str(name).lower()
        if lower in _lower_map:
            return _lower_map[lower]
        stripped = lower.replace("_", "").replace(" ", "")
        if stripped in _strip_map:
            return _strip_map[stripped]
        for col_lower, col_actual in _lower_map.items():
            if col_lower.startswith(lower) or lower.startswith(col_lower):
                return col_actual
        return None

    def _best_numeric_fallback(exclude: set | None = None) -> str | None:
        exclude = exclude or set()
        for col in profile.numeric_columns:
            if col not in exclude and col in df.columns:
                return col
        return None

    def _col_series(col_name: str):
        sel = df.loc[:, col_name]
        if isinstance(sel, pd.DataFrame):
            return sel.iloc[:, 0]
        return sel

    def _numeric_series(col_name: str):
        return pd.to_numeric(_col_series(col_name), errors="coerce")

    def _get_role_agg(col_name: str, default: str = "sum") -> str:
        role = (column_roles or {}).get(col_name, {}) if isinstance(column_roles, dict) else {}
        agg = str(role.get("agg") or default).strip().lower()
        return agg if agg in {"sum", "avg", "count", "nunique", "max", "min"} else default

    def _smart_top_n(measure_col: str, dimension_col: str, n: int = 12, agg: str = "sum"):
        grouped = _numeric_series(measure_col).groupby(_col_series(dimension_col))
        if agg == "avg":
            series = grouped.mean()
        elif agg == "count":
            series = grouped.count()
        elif agg == "max":
            series = grouped.max()
        elif agg == "min":
            series = grouped.min()
        else:
            series = grouped.sum()
        return series.nlargest(n)

    def _build_month_year_period_frame(measure_col: str):
        month_col = next((str(c) for c in df.columns if "month" in str(c).lower()), None)
        year_col = next((str(c) for c in df.columns if "year" in str(c).lower()), None)
        if not month_col or not year_col:
            return None

        month_raw = _col_series(month_col).astype(str).str.strip().str.lower()
        month_map = {
            "jan": 1, "january": 1,
            "feb": 2, "february": 2,
            "mar": 3, "march": 3,
            "apr": 4, "april": 4,
            "may": 5,
            "jun": 6, "june": 6,
            "jul": 7, "july": 7,
            "aug": 8, "august": 8,
            "sep": 9, "sept": 9, "september": 9,
            "oct": 10, "october": 10,
            "nov": 11, "november": 11,
            "dec": 12, "december": 12,
        }
        month_num = month_raw.map(month_map).fillna(pd.to_numeric(month_raw, errors="coerce"))
        year_num = pd.to_numeric(_col_series(year_col), errors="coerce")
        period_dt = pd.to_datetime(
            {"year": year_num, "month": month_num, "day": 1},
            errors="coerce",
        )
        if int(period_dt.notna().sum()) < 3:
            return None

        return pd.DataFrame({
            "_period": period_dt,
            "_label": period_dt.dt.strftime("%b-%Y"),
            "_measure": _numeric_series(measure_col),
        })

    specs = []
    position = 1
    _used_kpi_cols: set[str] = set()

    for spec in ai_specs:
        chart_type = str(spec.get("chart_type", "bar")).lower()
        title = str(spec.get("title", "Widget")).strip() or "Widget"

        raw_dimension = str(spec.get("dimension") or "").strip()
        raw_measures = spec.get("measures") or []
        if isinstance(raw_measures, str):
            raw_measures = [raw_measures]
        raw_measures = [str(m).strip() for m in raw_measures if str(m).strip()]
        raw_measure = raw_measures[0] if raw_measures else ""
        raw_x = str(spec.get("x_measure") or "").strip()
        raw_y = str(spec.get("y_measure") or "").strip()

        dimension = _resolve_col(raw_dimension) or ""
        measure = _resolve_col(raw_measure) or ""
        measures = [_resolve_col(m) for m in raw_measures]
        measures = [m for m in measures if m]
        x_measure = _resolve_col(raw_x) or ""
        y_measure = _resolve_col(raw_y) or ""

        palette = str(spec.get("palette") or "indigo").strip()
        if palette not in PALETTES:
            palette = "indigo"

        size = str(spec.get("size") or "md").strip()
        if size not in {"sm", "md", "lg"}:
            size = "md"

        ai_insight = str(spec.get("ai_insight") or "").strip()[:600]
        spec_agg = str(spec.get("_agg") or "").strip().lower()

        config: dict = {}

        try:
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
                specs.append({
                    "title": title,
                    "widget_type": "heading",
                    "position": position,
                    "config": config,
                })
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
                specs.append({
                    "title": title,
                    "widget_type": "text_canvas",
                    "position": position,
                    "config": config,
                })
                position += 1
                continue

            elif chart_type == "kpi":
                resolved_measure = measure if measure and measure in df.columns else None
                if not resolved_measure:
                    resolved_measure = _best_numeric_fallback(exclude=_used_kpi_cols)

                if resolved_measure:
                    _used_kpi_cols.add(resolved_measure)
                    role_info = column_roles.get(resolved_measure, {})
                    role_label = str(role_info.get("label") or "").strip()
                    human_label = role_label if role_label else _humanize_col(resolved_measure)

                    sem_type = str(role_info.get("data_type") or "").strip()
                    if not sem_type and getattr(profile, "column_types", None):
                        sem_type = profile.column_types.get(resolved_measure, {}).get("semantic_type", "")

                    kpi_meta = _detect_kpi_meta(resolved_measure, semantic_type=sem_type)

                    role_agg = str(role_info.get("agg") or "sum").strip()
                    if sem_type == "percentage" and not spec_agg:
                        role_agg = "avg"
                    agg = spec_agg if spec_agg else role_agg

                    numeric_col = _numeric_series(resolved_measure).dropna()

                    if agg == "nunique":
                        display_val = f"{int(_col_series(resolved_measure).nunique()):,}"
                        kpi_label = f"Unique {human_label}"
                    elif agg == "avg":
                        avg = numeric_col.mean() if len(numeric_col) else 0.0
                        prefix = kpi_meta.get("prefix", "")
                        suffix = kpi_meta.get("suffix", "")
                        display_val = f"{prefix}{avg:,.2f}{suffix}"
                        kpi_label = f"Avg {human_label}"
                    elif agg == "count":
                        display_val = f"{int(len(_col_series(resolved_measure).dropna())):,}"
                        kpi_label = f"{human_label} Count"
                    elif agg in ("max", "min"):
                        val = numeric_col.max() if agg == "max" else numeric_col.min()
                        prefix = kpi_meta.get("prefix", "")
                        suffix = kpi_meta.get("suffix", "")
                        display_val = f"{prefix}{val:,.2f}{suffix}" if len(numeric_col) else "0"
                        kpi_label = f"{'Peak' if agg == 'max' else 'Lowest'} {human_label}"
                    else:
                        total = numeric_col.sum() if len(numeric_col) else 0
                        prefix = kpi_meta.get("prefix", "")
                        suffix = kpi_meta.get("suffix", "")
                        if isinstance(total, (int, float)) and abs(total) >= 1_000_000:
                            display_val = f"{prefix}{total / 1_000_000:,.1f}M{suffix}"
                        elif isinstance(total, (int, float)) and abs(total) >= 1_000:
                            display_val = f"{prefix}{total:,.0f}{suffix}"
                        else:
                            display_val = f"{prefix}{total:,.2f}{suffix}" if prefix else f"{total:,.0f}"
                        kpi_label = human_label

                    if title and title.lower() not in ("widget", "kpi"):
                        kpi_label = title

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
                        ai_insight = f"{kpi_label}: {display_val}."

                else:
                    config = {
                        "kpi": title if title.lower() not in ("widget", "kpi") else "Dataset Records",
                        "value": f"{profile.total_rows:,}",
                        "kpi_meta": {"icon": "people", "format": "count", "prefix": "", "suffix": ""},
                        "layout": {"size": size},
                    }
                    if not ai_insight:
                        ai_insight = (
                            f"Dataset contains {profile.total_rows:,} records across "
                            f"{profile.total_columns} columns."
                        )

            elif chart_type == "bar" and dimension and measure and dimension in df.columns and measure in df.columns:
                top = _smart_top_n(measure, dimension, n=12, agg=_get_role_agg(measure, "sum"))
                labels = [str(l) for l in top.index]
                values = [round(float(v), 2) for v in top.values]
                config = _bar_config(
                    labels,
                    values,
                    _humanize_col(measure),
                    palette,
                    x_label=_humanize_col(dimension),
                    y_label=_humanize_col(measure),
                )
                config["layout"] = {"size": size}
                if not ai_insight and labels and values:
                    ai_insight, _ = ai_analyze_chart("bar", labels, values, title)

            elif chart_type == "hbar" and dimension and measure and dimension in df.columns and measure in df.columns:
                top = _smart_top_n(measure, dimension, n=12, agg=_get_role_agg(measure, "sum"))
                labels = [str(l) for l in top.index]
                values = [round(float(v), 2) for v in top.values]
                config = _hbar_config(
                    labels,
                    values,
                    _humanize_col(measure),
                    palette,
                    x_label=_humanize_col(measure),
                    y_label=_humanize_col(dimension),
                )
                config["layout"] = {"size": size}
                if not ai_insight and labels and values:
                    ai_insight, _ = ai_analyze_chart("hbar", labels, values, title)

            elif chart_type == "line" and dimension and measure and dimension in df.columns and measure in df.columns:
                merged_period = _build_month_year_period_frame(measure)
                if merged_period is not None and ("month" in dimension.lower() or "year" in dimension.lower()):
                    merged_period = merged_period.dropna(subset=["_period"]).sort_values("_period")
                    trend_data = merged_period.groupby(merged_period["_period"].dt.to_period("M"))["_measure"].sum()
                    labels = [str(p) for p in trend_data.index]
                    values = [round(float(v), 2) for v in trend_data.values]
                else:
                    tmp = pd.DataFrame({
                        "_dim": _col_series(dimension),
                        "_measure": _numeric_series(measure),
                    }).dropna(subset=["_dim", "_measure"])

                    try:
                        tmp["_dim_dt"] = pd.to_datetime(tmp["_dim"], errors="coerce")
                        if tmp["_dim_dt"].notna().sum() >= 3:
                            tmp = tmp.dropna(subset=["_dim_dt"]).sort_values("_dim_dt")
                            trend_data = tmp.groupby(tmp["_dim_dt"].dt.to_period("M"))["_measure"].sum()
                            labels = [str(p) for p in trend_data.index]
                        else:
                            trend_data = tmp.groupby("_dim")["_measure"].sum()
                            labels = [str(p) for p in trend_data.index]
                    except Exception:
                        trend_data = tmp.groupby("_dim")["_measure"].sum()
                        labels = [str(p) for p in trend_data.index]

                    values = [round(float(v), 2) for v in trend_data.values]

                config = _line_config(
                    labels,
                    values,
                    _humanize_col(measure),
                    palette,
                    x_label="Period" if "date" in dimension.lower() or "month" in dimension.lower() or "year" in dimension.lower() else _humanize_col(dimension),
                    y_label=_humanize_col(measure),
                )
                config["layout"] = {"size": size}
                if not ai_insight and labels and values:
                    ai_insight, _ = ai_analyze_chart("line", labels, values, title)

            elif chart_type == "area" and dimension and measure and dimension in df.columns and measure in df.columns:
                merged_period = _build_month_year_period_frame(measure)
                if merged_period is not None and ("month" in dimension.lower() or "year" in dimension.lower()):
                    merged_period = merged_period.dropna(subset=["_period"]).sort_values("_period")
                    trend_data = merged_period.groupby(merged_period["_period"].dt.to_period("M"))["_measure"].sum()
                    labels = [str(p) for p in trend_data.index]
                    values = [round(float(v), 2) for v in trend_data.values]
                    x_axis_label = "Period"
                else:
                    tmp = pd.DataFrame({
                        "_dim": _col_series(dimension),
                        "_measure": _numeric_series(measure),
                    }).dropna(subset=["_dim", "_measure"])

                    try:
                        tmp["_dim_dt"] = pd.to_datetime(tmp["_dim"], errors="coerce")
                        if tmp["_dim_dt"].notna().sum() >= 3:
                            tmp = tmp.dropna(subset=["_dim_dt"]).sort_values("_dim_dt")
                            trend_data = tmp.groupby(tmp["_dim_dt"].dt.to_period("M"))["_measure"].sum()
                            labels = [str(p) for p in trend_data.index]
                            x_axis_label = "Period"
                        else:
                            trend_data = tmp.groupby("_dim")["_measure"].sum()
                            labels = [str(p) for p in trend_data.index]
                            x_axis_label = _humanize_col(dimension)
                    except Exception:
                        trend_data = tmp.groupby("_dim")["_measure"].sum()
                        labels = [str(p) for p in trend_data.index]
                        x_axis_label = _humanize_col(dimension)

                    values = [round(float(v), 2) for v in trend_data.values]

                config = _area_config(
                    labels,
                    values,
                    _humanize_col(measure),
                    palette,
                    x_label=x_axis_label,
                    y_label=_humanize_col(measure),
                )
                config["layout"] = {"size": size}
                if not ai_insight and labels and values:
                    ai_insight, _ = ai_analyze_chart("area", labels, values, title)

            elif chart_type in ("pie", "doughnut") and dimension and dimension in df.columns:
                resolved_meas_for_pie = measure if measure and measure in df.columns else None
                vc = (
                    _numeric_series(resolved_meas_for_pie).groupby(_col_series(dimension)).sum().nlargest(8)
                    if resolved_meas_for_pie
                    else _col_series(dimension).value_counts().head(8)
                )
                labels = [str(l) for l in vc.index]
                values = [round(float(v), 2) for v in vc.values]
                fn = _pie_config if chart_type == "pie" else _doughnut_config
                config = fn(labels, values, palette)
                config["layout"] = {"size": size}
                if not ai_insight and labels and values:
                    ai_insight, _ = ai_analyze_chart(chart_type, labels, values, title)

            elif chart_type == "scatter":
                rx = x_measure if x_measure and x_measure in df.columns else None
                ry = y_measure if y_measure and y_measure in df.columns else None
                if not rx or not ry:
                    nums = [c for c in profile.numeric_columns if c in df.columns]
                    if len(nums) >= 2:
                        rx = rx or nums[0]
                        ry = ry or (nums[1] if nums[1] != rx else nums[2] if len(nums) > 2 else None)

                if rx and ry and rx in df.columns and ry in df.columns:
                    tmp = pd.DataFrame({
                        rx: _numeric_series(rx),
                        ry: _numeric_series(ry),
                    }).dropna().head(500)

                    x_vals = [round(float(v), 4) for v in tmp[rx]]
                    y_vals = [round(float(v), 4) for v in tmp[ry]]

                    from apps.datasets.services import _scatter_config as _sc
                    scatter_title = f"{_humanize_col(rx)} vs {_humanize_col(ry)}"
                    config = _sc(x_vals, y_vals, _humanize_col(rx), _humanize_col(ry), palette, scatter_title)
                    config["layout"] = {"size": size}
                    if not ai_insight:
                        ai_insight, _ = ai_analyze_chart("scatter", x_vals[:40], y_vals[:40], title)

            elif chart_type == "radar" and dimension and measure and dimension in df.columns and measure in df.columns:
                top = _numeric_series(measure).groupby(_col_series(dimension)).sum().nlargest(8)
                labels = [str(l) for l in top.index]
                values = [round(float(v), 2) for v in top.values]
                config = _radar_config(labels, values, _humanize_col(measure), palette)
                config["layout"] = {"size": size}
                if not ai_insight and labels and values:
                    ai_insight, _ = ai_analyze_chart("radar", labels, values, title)

            elif chart_type == "bubble":
                from apps.datasets.services import _bubble_config

                rx = x_measure if x_measure and x_measure in df.columns else None
                ry = y_measure if y_measure and y_measure in df.columns else None
                rr = measure if measure and measure in df.columns else None

                if not rx or not ry:
                    nums = [c for c in profile.numeric_columns if c in df.columns]
                    if len(nums) >= 2:
                        rx = rx or nums[0]
                        ry = ry or (nums[1] if len(nums) > 1 and nums[1] != rx else None)
                    if len(nums) >= 3 and not rr:
                        rr = nums[2] if nums[2] != rx and nums[2] != ry else None

                if rx and ry and rx in df.columns and ry in df.columns:
                    cols = [c for c in [rx, ry, rr] if c and c in df.columns]
                    cols = list(dict.fromkeys(cols))
                    tmp = df[cols].copy().head(200)

                    tmp[rx] = pd.to_numeric(tmp[rx], errors="coerce")
                    tmp[ry] = pd.to_numeric(tmp[ry], errors="coerce")
                    if rr and rr in tmp.columns:
                        tmp[rr] = pd.to_numeric(tmp[rr], errors="coerce")

                    tmp = tmp.dropna(subset=[rx, ry])

                    x_raw = tmp[rx].tolist()
                    y_raw = tmp[ry].tolist()

                    if rr and rr in tmp.columns:
                        r_raw = tmp[rr].fillna(0).tolist()
                        r_min = min(r_raw) if r_raw else 0
                        r_max = max(r_raw) if r_raw else 1
                        r_range = max(r_max - r_min, 1)
                        r_norm = [max(4, round((v - r_min) / r_range * 30 + 4, 1)) for v in r_raw]
                    else:
                        r_norm = [8] * len(x_raw)

                    data_pts = [
                        {"x": round(float(x), 4), "y": round(float(y), 4), "r": r}
                        for x, y, r in zip(x_raw, y_raw, r_norm)
                    ]

                    config = _bubble_config(
                        data_pts,
                        title,
                        palette,
                        x_label=_humanize_col(rx),
                        y_label=_humanize_col(ry),
                    )
                    config["layout"] = {"size": size}
                    if not ai_insight:
                        ai_insight = (
                            f"Bubble chart visualizing {_humanize_col(rx)} vs {_humanize_col(ry)}"
                            + (f" with bubble size representing {_humanize_col(rr)}" if rr else "")
                            + f" across {len(data_pts)} data points."
                        )

            elif chart_type == "polararea" and dimension and dimension in df.columns:
                from apps.datasets.services import _polararea_config

                resolved_meas = measure if measure and measure in df.columns else None
                vc = (
                    _numeric_series(resolved_meas).groupby(_col_series(dimension)).sum().nlargest(8)
                    if resolved_meas
                    else _col_series(dimension).value_counts().head(8)
                )
                labels = [str(l) for l in vc.index]
                values = [round(float(v), 2) for v in vc.values]
                config = _polararea_config(labels, values, palette)
                config["layout"] = {"size": size}
                if not ai_insight and labels and values:
                    ai_insight, _ = ai_analyze_chart("polararea", labels, values, title)

            elif chart_type == "mixed" and dimension and dimension in df.columns:
                from apps.datasets.services import _mixed_config

                bar_measures = [m for m in measures[:2] if m and m in df.columns and m != dimension]
                line_measures = [m for m in measures[2:4] if m and m in df.columns and m != dimension]
                if not bar_measures and measure and measure in df.columns and measure != dimension:
                    bar_measures = [measure]

                if bar_measures:
                    all_mix_cols = []
                    for candidate in bar_measures + line_measures:
                        if candidate in all_mix_cols:
                            continue
                        if _numeric_series(candidate).notna().sum() > 0:
                            all_mix_cols.append(candidate)

                    if not all_mix_cols:
                        continue

                    bar_measures = [m for m in bar_measures if m in all_mix_cols]
                    line_measures = [m for m in line_measures if m in all_mix_cols and m not in bar_measures]
                    if not bar_measures:
                        bar_measures = [all_mix_cols[0]]

                    mix_tmp = pd.DataFrame({"_dim": _col_series(dimension)})
                    for mc in all_mix_cols:
                        mix_tmp[mc] = _numeric_series(mc)

                    grouped = mix_tmp.groupby("_dim")[all_mix_cols].sum().head(12)
                    labels = [str(l) for l in grouped.index]

                    bar_ds = [
                        {"label": _humanize_col(m), "data": [round(float(v), 2) for v in grouped[m].tolist()]}
                        for m in bar_measures
                    ]
                    line_ds = [
                        {"label": _humanize_col(m), "data": [round(float(v), 2) for v in grouped[m].tolist()]}
                        for m in line_measures
                    ]

                    config = _mixed_config(
                        labels,
                        bar_ds,
                        line_ds,
                        palette,
                        x_label=_humanize_col(dimension),
                        y_label=_humanize_col(bar_measures[0]) if bar_measures else "",
                    )
                    config["layout"] = {"size": size}
                    if not ai_insight:
                        ai_insight = (
                            f"Dual-axis chart showing {', '.join(_humanize_col(m) for m in bar_measures)} "
                            f"across {len(labels)} {_humanize_col(dimension)} segments."
                        )

            elif chart_type == "funnel" and dimension and measure and dimension in df.columns and measure in df.columns:
                from apps.datasets.services import _funnel_config

                top = _numeric_series(measure).groupby(_col_series(dimension)).sum().nlargest(10)
                labels = [str(l) for l in top.index]
                values = [round(float(v), 2) for v in top.values]
                config = _funnel_config(labels, values, _humanize_col(measure), palette)
                config["layout"] = {"size": size}
                if not ai_insight and labels and values:
                    total = sum(values)
                    drop = round((values[0] - values[-1]) / values[0] * 100, 1) if values[0] else 0
                    ai_insight = (
                        f"Funnel shows {len(labels)} stages with total {total:,.0f} and {drop}% drop-off."
                    )

            elif chart_type == "gauge" and measure and measure in df.columns:
                from apps.datasets.services import _gauge_config

                col_data = _numeric_series(measure).dropna()
                if len(col_data) > 0:
                    val = float(col_data.mean())
                    min_v = float(col_data.min())
                    max_v = float(col_data.max())
                    config = _gauge_config(val, min_v, max_v, _humanize_col(measure), palette)
                    config["layout"] = {"size": size}
                    pct = round((val - min_v) / max(max_v - min_v, 1) * 100, 1)
                    if not ai_insight:
                        ai_insight = (
                            f"Average {_humanize_col(measure)} is {val:,.2f}, at {pct}% of the observed range."
                        )

            elif chart_type == "waterfall" and dimension and measure and dimension in df.columns and measure in df.columns:
                from apps.datasets.services import _waterfall_config

                grouped = _numeric_series(measure).groupby(_col_series(dimension)).sum().head(10)
                labels = [str(l) for l in grouped.index]
                values = [round(float(v), 2) for v in grouped.values]
                config = _waterfall_config(
                    labels,
                    values,
                    _humanize_col(measure),
                    palette,
                    x_label=_humanize_col(dimension),
                    y_label=_humanize_col(measure),
                )
                config["layout"] = {"size": size}
                if not ai_insight and labels and values:
                    pos = sum(v for v in values if v > 0)
                    neg = sum(v for v in values if v < 0)
                    ai_insight = (
                        f"Waterfall breakdown: +{pos:,.0f} gains vs {neg:,.0f} deductions."
                    )

            elif chart_type == "table":
                all_candidates = ([dimension] if dimension else []) + measures
                cols = [c for c in all_candidates if c and c in df.columns]

                if not cols:
                    date_like = [
                        c for c in df.columns
                        if any(k in str(c).lower() for k in ["date", "month", "year", "period", "quarter"])
                    ]
                    cols = (
                        date_like[:1]
                        + [c for c in profile.categorical_columns[:2] if c in df.columns]
                        + [c for c in profile.numeric_columns[:4] if c in df.columns]
                    )[:6]

                if not cols:
                    cols = [str(c) for c in df.columns[:6]]

                sort_col = next((c for c in cols if c in profile.numeric_columns and c in df.columns), None)

                try:
                    preview = df[cols].copy()
                    if sort_col:
                        preview[sort_col] = pd.to_numeric(preview[sort_col], errors="coerce")
                        preview = preview.sort_values(sort_col, ascending=False)
                    preview = preview.head(100).fillna("")
                except Exception:
                    preview = df[cols].head(100).fillna("")

                rows = [[str(v) for v in row] for row in preview.values.tolist()]
                config = {"columns": cols, "rows": rows, "layout": {"size": size}}
                if not ai_insight:
                    ai_insight = f"Showing top {len(rows)} records across {len(cols)} columns."

        except Exception as exc:
            logger.warning("Widget spec build failed for '%s' (%s): %s", title, chart_type, exc)
            continue

        if not config:
            logger.debug("Skipping '%s' (%s) — no config produced", title, chart_type)
            continue

        if ai_insight:
            config["ai_insight"] = ai_insight[:600]

        config["builder"] = {
            "dimension": dimension,
            "measures": [m for m in measures if m in df.columns],
            "measure": measure,
            "x_measure": x_measure,
            "y_measure": y_measure,
            "x_label": _humanize_col(dimension) if dimension else "",
            "y_label": _humanize_col(measure) if measure else "",
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