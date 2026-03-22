"""Celery tasks for background dashboard/chart generation."""
import logging
from pathlib import Path

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=5)
def build_dashboard_widgets(self, dashboard_id: str, version_id: int):
    """Background task: generate all widgets for a newly created dashboard.

    Runs after the dashboard record is created so the user can be redirected
    immediately.  Sets dashboard.build_status → 'building' then 'ready'/'failed'.

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
    import pandas as pd
    from apps.dashboards.models import Dashboard, DashboardWidget
    from apps.datasets.models import DatasetVersion
    from apps.datasets.services import (
        PALETTES,
        _area_config,
        _bar_config,
        _doughnut_config,
        _hbar_config,
        _line_config,
        _pie_config,
        _radar_config,
        _scatter_config,
        _humanize_col,
        _detect_kpi_meta,
        _compute_kpi_trend,
        ai_generate_dashboard_specs,
        ai_generate_dashboard_title,
        ai_suggest_slicers,
        ai_detect_column_roles,
        ai_generate_comprehensive_insights,
        build_profile_summary,
        generate_widget_specs_from_version,
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

            # ── Step 1: AI column role detection ──────────────────────────────
            logger.info("Dashboard %s: detecting column roles via AI", dashboard_id)
            try:
                column_roles = ai_detect_column_roles(df, profile)
                logger.info("Column roles detected: %d columns classified", len(column_roles))
            except Exception as exc:
                logger.warning("Column role detection failed: %s", exc)
                column_roles = {}

            # ── Step 2: AI-powered title ───────────────────────────────────────
            logger.info("Dashboard %s: generating title", dashboard_id)
            ai_title = ai_generate_dashboard_title(df, profile, dataset_version.dataset.name)
            if ai_title:
                dashboard.title = ai_title
                dashboard.save(update_fields=["title"])

            # ── Step 3: AI slicer suggestions ──────────────────────────────────
            logger.info("Dashboard %s: generating slicer suggestions", dashboard_id)
            slicer_suggestions, _ = ai_suggest_slicers(df, profile)
            if slicer_suggestions:
                auto_filters = [
                    {
                        "id": s["column"],
                        "column": s["column"],
                        "filter_type": s["filter_type"],
                        "label": s["label"],
                        "version_id": None,
                    }
                    for s in slicer_suggestions
                ]
                dashboard.filter_config = auto_filters
                dashboard.save(update_fields=["filter_config"])

            # ── Step 4: Comprehensive dashboard spec ────────────────────────────
            logger.info("Dashboard %s: generating comprehensive dashboard specs", dashboard_id)
            ai_specs = ai_generate_dashboard_specs(df, profile)

            if ai_specs:
                logger.info("AI specs generated: %d widgets planned", len(ai_specs))
                widget_specs = _build_widget_specs_from_ai(ai_specs, df, profile, column_roles)
            else:
                logger.info("AI specs unavailable; using heuristic generator")
                widget_specs = generate_widget_specs_from_version(dataset_version)

            # ── Step 5: Comprehensive AI narrative widget ───────────────────────
            logger.info("Dashboard %s: generating comprehensive insights narrative", dashboard_id)
            try:
                widget_titles = [s.get("title", "") for s in widget_specs if s.get("title")]
                narrative_data = ai_generate_comprehensive_insights(
                    df, profile, dashboard.title, widget_titles
                )
                narrative_widget = _build_narrative_widget(narrative_data, dashboard.title)
                if narrative_widget:
                    # Insert at position 0 (before everything else)
                    widget_specs = [narrative_widget] + widget_specs
                    # Re-number positions
                    for i, s in enumerate(widget_specs):
                        s["position"] = i + 1
            except Exception as exc:
                logger.warning("Narrative generation failed: %s", exc)

        else:
            widget_specs = generate_widget_specs_from_version(dataset_version)

        # ── Save widgets to DB ────────────────────────────────────────────────
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
                chart_config={
                    "type": "bar",
                    "data": {
                        "labels": ["A", "B", "C", "D"],
                        "datasets": [{"label": "Count", "data": [40, 30, 20, 10],
                                      "backgroundColor": ["#6366f1", "#8b5cf6", "#a78bfa", "#c4b5fd"],
                                      "borderRadius": 6}],
                    },
                    "options": {"responsive": True, "maintainAspectRatio": False,
                                "plugins": {"legend": {"display": False}}},
                },
            )

        dashboard.build_status = Dashboard.BuildStatus.READY
        dashboard.save(update_fields=["build_status"])
        logger.info("Dashboard %s built successfully with %d widgets", dashboard_id, len(widget_specs or []))

    except Exception as exc:
        logger.exception("build_dashboard_widgets failed for %s: %s", dashboard_id, exc)
        try:
            dashboard.build_status = Dashboard.BuildStatus.FAILED
            dashboard.save(update_fields=["build_status"])
        except Exception:
            pass
        raise self.retry(exc=exc)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _load_df(dataset_version):
    """Load DataFrame from a DatasetVersion's source file."""
    import pandas as pd
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
    """Convert AI-generated dashboard spec list into concrete widget specs.

    Enhancements over the basic version:
    - KPI widgets: humanized labels, proper icon metadata, trend computation
    - Chart widgets: per-widget AI analysis if AI insight not already provided
    - Heading widgets: injected as section separators (blue gradient style)
    - Text canvas (narrative): preserved with special is_narrative flag
    - All widgets get ai_insight populated at build time
    """
    import pandas as pd
    from apps.datasets.services import (
        PALETTES,
        _area_config, _bar_config, _doughnut_config, _hbar_config,
        _line_config, _pie_config, _radar_config, _scatter_config,
        _humanize_col, _detect_kpi_meta, _compute_kpi_trend,
        ai_analyze_chart,
    )

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

        # ── Handle layout/structural widgets ─────────────────────────────────
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
            # Narrative widget: preserve the rich narrative data
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

        # ── KPI widget ────────────────────────────────────────────────────────
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
                    kpi_meta = _detect_kpi_meta(resolved_measure)
                    agg = spec_agg if spec_agg else str(role_info.get("agg") or "sum").strip()

                    # Smart value formatting based on aggregation preference
                    if agg == "nunique":
                        display_val = f"{int(df[resolved_measure].nunique()):,}"
                        kpi_label = f"Unique {human_label}"
                        avg = 0.0
                    elif agg == "avg":
                        col_data = df[resolved_measure].dropna()
                        avg = col_data.mean()
                        display_val = f"{avg:,.2f}"
                        kpi_label = f"Avg {human_label}"
                    elif agg == "count":
                        col_data = df[resolved_measure].dropna()
                        avg = col_data.mean() if pd.api.types.is_numeric_dtype(col_data) else 0.0
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

                    # Build KPI-specific insight if not already set
                    if not ai_insight:
                        try:
                            if agg == "nunique":
                                ai_insight = f"{kpi_label}: {display_val} distinct values across {profile.total_rows:,} records."
                            else:
                                col_data = df[resolved_measure].dropna()
                                avg = col_data.mean()
                                pct_above_avg = round(sum(1 for v in col_data if v > avg) / len(col_data) * 100, 1)
                                ai_insight = (
                                    f"{kpi_label} totals {display_val} with a mean of {avg:,.2f}. "
                                    f"{pct_above_avg}% of records exceed the average."
                                )
                        except Exception:
                            pass
                else:
                    config = {"kpi": "Total Records", "value": f"{profile.total_rows:,}", "kpi_meta": {"icon": "people", "format": "count"}, "layout": {"size": size}}
                    if not ai_insight:
                        ai_insight = f"This dashboard analyzes {profile.total_rows:,} records across {profile.total_columns} dimensions."

            # ── Bar chart ─────────────────────────────────────────────────────
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

            # ── Horizontal bar ────────────────────────────────────────────────
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

            # ── Line chart ────────────────────────────────────────────────────
            elif chart_type == "line" and dimension and measure and dimension in df.columns and measure in df.columns:
                tmp = df[[dimension, measure]].copy()
                try:
                    tmp[dimension] = pd.to_datetime(tmp[dimension], errors="coerce")
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

            # ── Area chart ────────────────────────────────────────────────────
            elif chart_type == "area" and dimension and measure and dimension in df.columns and measure in df.columns:
                tmp = df[[dimension, measure]].copy()
                try:
                    tmp[dimension] = pd.to_datetime(tmp[dimension], errors="coerce")
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

            # ── Pie / Doughnut ────────────────────────────────────────────────
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

            # ── Scatter ───────────────────────────────────────────────────────
            elif chart_type == "scatter" and x_measure and y_measure and x_measure in df.columns and y_measure in df.columns:
                tmp = df[[x_measure, y_measure]].dropna().head(500)
                x_vals = [round(float(v), 4) for v in tmp[x_measure]]
                y_vals = [round(float(v), 4) for v in tmp[y_measure]]
                from apps.datasets.services import _scatter_config as _sc
                config = _sc(x_vals, y_vals, x_measure, y_measure, palette, f"{x_measure} vs {y_measure}")
                config["layout"] = {"size": size}
                if not ai_insight:
                    try:
                        ai_insight, _ = ai_analyze_chart("scatter", x_vals[:40], y_vals[:40], title)
                    except Exception:
                        pass

            # ── Radar ─────────────────────────────────────────────────────────
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

            # ── Table ─────────────────────────────────────────────────────────
            elif chart_type == "table":
                cols = [c for c in (([dimension] + measures) if dimension else measures) if c and c in df.columns]
                if not cols:
                    cols = [str(c) for c in df.columns[:6]]
                preview = df[cols].head(50).fillna("")
                rows = [[str(v) for v in row] for row in preview.values.tolist()]
                config = {"columns": cols, "rows": rows, "layout": {"size": size}}
                if not ai_insight:
                    ai_insight = (
                        f"This table shows {len(rows)} records across {len(cols)} columns: "
                        f"{', '.join(_humanize_col(c) for c in cols[:3])}{'...' if len(cols) > 3 else ''}. "
                        f"Sort and filter to identify top performers and outliers."
                    )

        except Exception as exc:
            logger.warning("Widget spec build failed for '%s' (%s): %s", title, chart_type, exc)
            continue

        if not config:
            continue

        # Attach AI insight
        if ai_insight:
            config["ai_insight"] = ai_insight[:400]

        # Attach builder metadata for filter rebuilding
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
