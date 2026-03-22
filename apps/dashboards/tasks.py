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
    """
    import pandas as pd
    from apps.dashboards.models import Dashboard, DashboardDataset, DashboardWidget
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
        ai_generate_dashboard_specs,
        ai_generate_dashboard_title,
        ai_suggest_slicers,
        build_profile_summary,
        generate_widget_specs_from_version,
        _compute_kpi_trend,
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

        # ── AI-powered or heuristic title update ────────────────────────────
        if df is not None:
            profile = build_profile_summary(df)
            ai_title = ai_generate_dashboard_title(df, profile, dataset_version.dataset.name)
            if ai_title:
                dashboard.title = ai_title
                dashboard.save(update_fields=["title"])

        # ── Generate widget specs ────────────────────────────────────────────
        if df is not None:
            profile = build_profile_summary(df)
            ai_specs = ai_generate_dashboard_specs(df, profile)

            # Auto-save slicer suggestions
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

            if ai_specs:
                widget_specs = _build_widget_specs_from_ai(ai_specs, df, profile)
            else:
                widget_specs = generate_widget_specs_from_version(dataset_version)
        else:
            widget_specs = generate_widget_specs_from_version(dataset_version)

        # ── Create widget DB records ─────────────────────────────────────────
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


# ── Helpers (mirror of views.py helpers, kept here to avoid circular import) ──


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


def _build_widget_specs_from_ai(ai_specs: list, df, profile) -> list[dict]:
    """Convert AI-generated dashboard spec list into concrete widget specs."""
    import pandas as pd
    from apps.datasets.services import (
        PALETTES, _area_config, _bar_config, _doughnut_config, _hbar_config,
        _line_config, _pie_config, _radar_config, _scatter_config, _compute_kpi_trend,
    )

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
        ai_insight = str(spec.get("ai_insight") or "").strip()[:300]
        config: dict = {}
        try:
            if chart_type == "kpi":
                if measure and measure in df.columns:
                    total = df[measure].sum()
                    formatted = f"{total:,.0f}"
                    config = {"kpi": measure, "value": formatted, "layout": {"size": size}}
                    trend = _compute_kpi_trend(df, measure)
                    if trend:
                        config["trend"] = trend
                else:
                    config = {"kpi": "rows", "value": f"{profile.total_rows:,}", "layout": {"size": size}}
            elif chart_type == "bar" and dimension and measure and dimension in df.columns and measure in df.columns:
                top = df.groupby(dimension)[measure].sum().nlargest(10)
                config = _bar_config([str(l) for l in top.index], [round(float(v), 2) for v in top.values], measure, palette)
                config["layout"] = {"size": size}
            elif chart_type == "hbar" and dimension and measure and dimension in df.columns and measure in df.columns:
                top = df.groupby(dimension)[measure].sum().nlargest(10)
                config = _hbar_config([str(l) for l in top.index], [round(float(v), 2) for v in top.values], measure, palette)
                config["layout"] = {"size": size}
            elif chart_type == "line" and dimension and measure and dimension in df.columns and measure in df.columns:
                tmp = df[[dimension, measure]].copy()
                try:
                    tmp[dimension] = pd.to_datetime(tmp[dimension], errors="coerce")
                    tmp = tmp.dropna(subset=[dimension])
                    trend_data = tmp.groupby(tmp[dimension].dt.to_period("M"))[measure].sum()
                except Exception:
                    trend_data = tmp.groupby(dimension)[measure].sum()
                config = _line_config([str(p) for p in trend_data.index], [round(float(v), 2) for v in trend_data.values], measure, palette)
                config["layout"] = {"size": size}
            elif chart_type == "area" and dimension and measure and dimension in df.columns and measure in df.columns:
                tmp = df[[dimension, measure]].copy()
                try:
                    tmp[dimension] = pd.to_datetime(tmp[dimension], errors="coerce")
                    tmp = tmp.dropna(subset=[dimension])
                    trend_data = tmp.groupby(tmp[dimension].dt.to_period("M"))[measure].sum()
                except Exception:
                    trend_data = tmp.groupby(dimension)[measure].sum()
                config = _area_config([str(p) for p in trend_data.index], [round(float(v), 2) for v in trend_data.values], measure, palette)
                config["layout"] = {"size": size}
            elif chart_type in ("pie", "doughnut") and dimension and dimension in df.columns:
                vc = (
                    df.groupby(dimension)[measure].sum().nlargest(6)
                    if measure and measure in df.columns
                    else df[dimension].value_counts().head(6)
                )
                fn = _pie_config if chart_type == "pie" else _doughnut_config
                config = fn([str(l) for l in vc.index], [round(float(v), 2) for v in vc.values], palette)
                config["layout"] = {"size": size}
            elif chart_type == "scatter" and x_measure and y_measure and x_measure in df.columns and y_measure in df.columns:
                tmp = df[[x_measure, y_measure]].dropna().head(500)
                config = _scatter_config(
                    [round(float(v), 4) for v in tmp[x_measure]],
                    [round(float(v), 4) for v in tmp[y_measure]],
                    x_measure, y_measure, palette, f"{x_measure} vs {y_measure}",
                )
                config["layout"] = {"size": size}
            elif chart_type == "radar" and dimension and measure and dimension in df.columns and measure in df.columns:
                top = df.groupby(dimension)[measure].sum().nlargest(8)
                config = _radar_config([str(l) for l in top.index], [round(float(v), 2) for v in top.values], measure, palette)
                config["layout"] = {"size": size}
            elif chart_type == "table":
                cols = [c for c in (([dimension] + measures) if dimension else measures) if c and c in df.columns]
                if not cols:
                    cols = [str(c) for c in df.columns[:5]]
                preview = df[cols].head(50).fillna("")
                rows = [[str(v) for v in row] for row in preview.values.tolist()]
                config = {"columns": cols, "rows": rows, "layout": {"size": size}}
        except Exception as e:
            logger.warning("Widget spec build failed for %s: %s", chart_type, e)
            continue

        if not config:
            continue

        if ai_insight:
            config["ai_insight"] = ai_insight

        specs.append({
            "title": title,
            "widget_type": chart_type if chart_type != "area" else "area",
            "position": position,
            "config": config,
        })
        position += 1

    return specs
