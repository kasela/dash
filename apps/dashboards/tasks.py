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
        _multi_bar_config,
        _doughnut_config,
        _hbar_config,
        _line_config,
        _multi_line_config,
        _pie_config,
        _radar_config,
        _scatter_config,
        _humanize_col,
        _detect_kpi_meta,
        _compute_kpi_trend,
        detect_and_clean_headers,
        deduplicate_chart_specs,
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
                auto_filters = []
                for s in slicer_suggestions:
                    col = s["column"]
                    raw_label = str(s.get("label") or "").strip()
                    # If AI returned the raw column name or something that still looks like a
                    # snake_case/camelCase identifier, humanize it for a clean UI label.
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

            # ── Step 4: Comprehensive dashboard spec ────────────────────────────
            logger.info("Dashboard %s: generating comprehensive dashboard specs (plan=%s)", dashboard_id, plan)
            ai_specs = ai_generate_dashboard_specs(
                df, profile, dataset_version.dataset.name,
                plan=plan,
                column_roles=column_roles,
            )

            if ai_specs:
                logger.info("AI specs generated: %d widgets planned", len(ai_specs))
                widget_specs = _build_widget_specs_from_ai(ai_specs, df, profile, column_roles)
                # Deduplicate charts to ensure every widget shows unique data
                widget_specs = deduplicate_chart_specs(widget_specs)
                logger.info("After deduplication: %d widgets retained", len(widget_specs))
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
    from apps.datasets.services import detect_and_clean_headers
    try:
        file_path = dataset_version.source_file.path
        name = Path(file_path).name.lower()
        if name.endswith(".csv"):
            df = pd.read_csv(file_path)
        elif name.endswith((".xlsx", ".xlsm")):
            df = pd.read_excel(file_path)
        elif name.endswith(".json"):
            df = pd.read_json(file_path)
        else:
            return None
        return detect_and_clean_headers(df)
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

    Key improvements:
    - Robust column resolution: case-insensitive + strip + partial prefix matching for ALL chart types
    - KPI fallback: when AI column can't be resolved, pick the best numeric column instead of "Total Records"
    - Business-grade labels: always humanize dimension/measure names for axis labels and dataset labels
    - Per-widget AI analysis enrichment for all chart types
    """
    import pandas as pd
    from apps.datasets.services import (
        PALETTES,
        _area_config, _bar_config, _doughnut_config, _hbar_config,
        _line_config, _pie_config, _radar_config, _scatter_config,
        _multi_bar_config, _multi_line_config,
        _humanize_col, _detect_kpi_meta, _compute_kpi_trend,
        ai_analyze_chart,
    )
    _ = (_multi_bar_config, _multi_line_config)

    if column_roles is None:
        column_roles = {}

    # ── Column resolution helper ─────────────────────────────────────────────
    # Build lookup maps once so every widget can resolve AI-suggested column names
    # that may differ in casing, spacing, or have slight paraphrases.
    _lower_map: dict[str, str] = {c.lower(): c for c in df.columns}
    _strip_map: dict[str, str] = {c.lower().replace("_", "").replace(" ", ""): c for c in df.columns}

    def _resolve_col(name: str) -> str | None:
        """Return the exact df column name matching `name`, or None if not found.

        Resolution order:
        1. Exact match
        2. Case-insensitive match
        3. Stripped (remove underscores/spaces) case-insensitive match
        4. Prefix match (AI truncated the name)
        """
        if not name:
            return None
        if name in df.columns:
            return name
        lower = name.lower()
        if lower in _lower_map:
            return _lower_map[lower]
        stripped = lower.replace("_", "").replace(" ", "")
        if stripped in _strip_map:
            return _strip_map[stripped]
        # Prefix / contains match as last resort
        for col_lower, col_actual in _lower_map.items():
            if col_lower.startswith(lower) or lower.startswith(col_lower):
                return col_actual
        return None

    # ── Smart KPI fallback column picker ─────────────────────────────────────
    def _best_numeric_fallback(exclude: set | None = None) -> str | None:
        """Pick the most meaningful numeric column when AI suggestion can't be resolved."""
        exclude = exclude or set()
        for col in profile.numeric_columns:
            if col not in exclude and pd.api.types.is_numeric_dtype(df[col]):
                return col
        return None

    def _col_series(name: str):
        """Return a 1-D Series for a column, even if duplicate column names exist."""
        col_obj = df.loc[:, name]
        if isinstance(col_obj, pd.DataFrame):
            return col_obj.iloc[:, 0]
        return col_obj

    def _numeric_series(name: str):
        return pd.to_numeric(_col_series(name), errors="coerce")

    def _to_datetime_series(series):
        """Coerce datetime values while avoiding noisy format-inference warnings."""
        import warnings
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                return pd.to_datetime(series, format="mixed", errors="coerce")
        except TypeError:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                return pd.to_datetime(series, errors="coerce")

    # ── Enhancement helpers ──────────────────────────────────────────────────

    def _get_role_agg(col_name: str) -> str:
        """Return the correct aggregation method for a column based on its role/semantic type."""
        if col_name in column_roles:
            agg = str(column_roles[col_name].get("agg") or "sum").strip().lower()
            if agg in ("sum", "avg", "count", "nunique", "max", "min"):
                return agg
        if profile.column_types:
            sem = profile.column_types.get(col_name, {}).get("semantic_type", "")
            if sem == "percentage":
                return "avg"
        return "sum"

    def _smart_groupby(dim_series, val_series, agg: str = "sum", top_n: int = 12):
        """Group val_series by dim_series using the specified aggregation, return top N."""
        tmp = pd.DataFrame({
            "_d": dim_series,
            "_v": pd.to_numeric(val_series, errors="coerce"),
        }).dropna(subset=["_d", "_v"])
        grp = tmp.groupby("_d")["_v"]
        agg_fns = {
            "avg": lambda g: g.mean(),
            "count": lambda g: g.count(),
            "nunique": lambda g: g.nunique(),
            "max": lambda g: g.max(),
            "min": lambda g: g.min(),
        }
        result = agg_fns.get(agg, lambda g: g.sum())(grp)
        return result.nlargest(top_n)

    def _get_date_granularity(dt_series) -> str:
        """Return optimal pandas period alias based on the date range span."""
        try:
            clean = dt_series.dropna()
            if len(clean) < 2:
                return "M"
            span = int((clean.max() - clean.min()).days)
            if span < 60:
                return "D"
            elif span < 180:
                return "W"
            elif span < 730:
                return "M"
            elif span < 3650:
                return "Q"
            else:
                return "A"
        except Exception:
            return "M"

    _GRAN_LABELS = {"D": "Daily", "W": "Weekly", "M": "Monthly", "Q": "Quarterly", "A": "Yearly"}

    def _smart_top_n(col_name: str, default: int = 12) -> int:
        """Choose top-N chart limit based on dimension column cardinality."""
        try:
            card = int(df[col_name].nunique(dropna=True))
            if card <= 8:
                return card
            elif card <= 20:
                return min(card, 15)
            elif card <= 50:
                return 15
            else:
                return default
        except Exception:
            return default

    def _flag_large_numbers(config: dict, values) -> dict:
        """Set _large_num_fmt flag on config when values are large (≥10K)."""
        try:
            flat_vals = [v for v in values if isinstance(v, (int, float)) and v is not None]
            if flat_vals and max(abs(v) for v in flat_vals) >= 10_000:
                config["_large_num_fmt"] = True
        except Exception:
            pass
        return config

    specs = []
    position = 1
    _used_kpi_cols: set[str] = set()

    for spec in ai_specs:
        chart_type = str(spec.get("chart_type", "bar")).lower()
        title = str(spec.get("title", "Widget")).strip() or "Widget"

        # Resolve dimension + measure with robust matching
        raw_dimension = str(spec.get("dimension") or "").strip()
        raw_measures = spec.get("measures") or []
        if isinstance(raw_measures, str):
            raw_measures = [raw_measures]
        raw_measures = [str(m).strip() for m in raw_measures if str(m).strip()]
        raw_measure = raw_measures[0] if raw_measures else ""
        raw_x = str(spec.get("x_measure") or "").strip()
        raw_y = str(spec.get("y_measure") or "").strip()

        # Resolved names (may be None if not found)
        dimension = _resolve_col(raw_dimension) or ""
        measure = _resolve_col(raw_measure) or ""
        measures = [_resolve_col(m) or m for m in raw_measures]
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

        # ── Layout / structural widgets ───────────────────────────────────────
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

        # ── KPI widget ────────────────────────────────────────────────────────
        try:
            if chart_type == "kpi":
                resolved_measure = measure if measure and measure in df.columns else None

                # Smart fallback: pick an unused numeric column rather than "Total Records"
                if not resolved_measure:
                    resolved_measure = _best_numeric_fallback(exclude=_used_kpi_cols)

                if resolved_measure:
                    _used_kpi_cols.add(resolved_measure)
                    role_info = column_roles.get(resolved_measure, {})
                    role_label = str(role_info.get("label") or "").strip()
                    human_label = role_label if role_label else _humanize_col(resolved_measure)
                    # Use data_type from column_roles or profile.column_types for richer KPI meta
                    sem_type = str(role_info.get("data_type") or "").strip()
                    if not sem_type and profile.column_types:
                        sem_type = profile.column_types.get(resolved_measure, {}).get("semantic_type", "")
                    kpi_meta = _detect_kpi_meta(resolved_measure, semantic_type=sem_type)
                    # Use aggregation from column_roles if not explicitly specified
                    role_agg = str(role_info.get("agg") or "sum").strip()
                    if sem_type == "percentage" and not spec_agg:
                        role_agg = "avg"  # percentages should always be averaged
                    agg = spec_agg if spec_agg else role_agg

                    if agg == "nunique":
                        display_val = f"{int(df[resolved_measure].nunique()):,}"
                        kpi_label = f"Unique {human_label}"
                        col_data = pd.Series(dtype=float)
                    elif agg == "avg":
                        col_data = df[resolved_measure].dropna()
                        avg = col_data.mean() if pd.api.types.is_numeric_dtype(col_data) else 0.0
                        prefix = kpi_meta.get("prefix", "")
                        suffix = kpi_meta.get("suffix", "")
                        display_val = f"{prefix}{avg:,.2f}{suffix}"
                        kpi_label = f"Avg {human_label}"
                    elif agg == "count":
                        col_data = df[resolved_measure].dropna()
                        display_val = f"{int(len(col_data)):,}"
                        kpi_label = f"{human_label} Count"
                    elif agg in ("max", "min"):
                        col_data = df[resolved_measure].dropna()
                        val = col_data.max() if agg == "max" else col_data.min()
                        prefix = kpi_meta.get("prefix", "")
                        suffix = kpi_meta.get("suffix", "")
                        display_val = f"{prefix}{val:,.2f}{suffix}" if pd.api.types.is_numeric_dtype(col_data) else str(val)
                        kpi_label = f"{'Peak' if agg == 'max' else 'Lowest'} {human_label}"
                        col_data = col_data  # keep for trend
                    else:  # sum / default
                        col_data = df[resolved_measure].dropna()
                        total = col_data.sum() if pd.api.types.is_numeric_dtype(col_data) else 0
                        prefix = kpi_meta.get("prefix", "")
                        suffix = kpi_meta.get("suffix", "")
                        # Smart number formatting: abbreviate large values
                        if isinstance(total, (int, float)) and abs(total) >= 1_000_000:
                            display_val = f"{prefix}{total / 1_000_000:,.1f}M{suffix}"
                        elif isinstance(total, (int, float)) and abs(total) >= 1_000:
                            display_val = f"{prefix}{total:,.0f}{suffix}"
                        else:
                            display_val = f"{prefix}{total:,.2f}{suffix}" if prefix else f"{total:,.0f}"
                        kpi_label = human_label

                    # Use AI-provided name as the KPI label when available (it's business-friendly)
                    if title and title.lower() not in ("widget", "kpi"):
                        kpi_label = title

                    config = {
                        "kpi": kpi_label,
                        "value": display_val,
                        "kpi_meta": kpi_meta,
                        "layout": {"size": size},
                    }

                    # Attach rich trend / stats
                    if agg not in ("nunique", "count"):
                        trend = _compute_kpi_trend(df, resolved_measure)
                        if trend:
                            config["trend"] = trend
                    elif pd.api.types.is_numeric_dtype(df[resolved_measure]):
                        try:
                            c = df[resolved_measure].dropna()
                            config["trend"] = {
                                "trend_dir": "flat",
                                "trend_pct": 0.0,
                                "sparkline": [],
                                "sparkline_pct": [],
                                "avg": round(float(c.mean()), 2),
                                "max_val": round(float(c.max()), 2),
                                "min_val": round(float(c.min()), 2),
                                "median_val": round(float(c.median()), 2),
                                "p25": round(float(c.quantile(0.25)), 2),
                                "p75": round(float(c.quantile(0.75)), 2),
                                "count": len(c),
                                "secondary_label": "records",
                                "secondary_value": f"{len(c):,}",
                            }
                        except Exception:
                            pass

                    # Enrich insight
                    if not ai_insight:
                        try:
                            if agg == "nunique":
                                ai_insight = (
                                    f"{kpi_label}: {display_val} distinct values across "
                                    f"{profile.total_rows:,} records "
                                    f"({round(int(df[resolved_measure].nunique()) / max(profile.total_rows, 1) * 100, 1)}% unique rate)."
                                )
                            elif agg in ("avg", "sum"):
                                c = df[resolved_measure].dropna()
                                avg_v = float(c.mean())
                                median_v = float(c.median())
                                p75_v = float(c.quantile(0.75))
                                pct_above = round(float((c > avg_v).mean()) * 100, 1)
                                ai_insight = (
                                    f"{kpi_label}: {display_val}. "
                                    f"Mean {avg_v:,.2f} · Median {median_v:,.2f} · 75th pct {p75_v:,.2f}. "
                                    f"{pct_above}% of records exceed the mean."
                                )
                            else:
                                ai_insight = (
                                    f"{kpi_label}: {display_val} across {profile.total_rows:,} records."
                                )
                        except Exception:
                            pass
                else:
                    # Absolute last-resort: dataset summary KPI
                    config = {
                        "kpi": title if title.lower() not in ("widget", "kpi") else "Dataset Records",
                        "value": f"{profile.total_rows:,}",
                        "kpi_meta": {"icon": "people", "format": "count", "prefix": "", "suffix": ""},
                        "layout": {"size": size},
                    }
                    if not ai_insight:
                        ai_insight = (
                            f"Dataset contains {profile.total_rows:,} records across "
                            f"{profile.total_columns} columns "
                            f"({len(profile.numeric_columns)} numeric, {len(profile.categorical_columns)} categorical)."
                        )

            # ── Bar chart ─────────────────────────────────────────────────────
            elif chart_type == "bar" and dimension and dimension in df.columns:
                valid_measures = [m for m in measures if m and m in df.columns]
                if not valid_measures and measure and measure in df.columns:
                    valid_measures = [measure]
                if valid_measures:
                    top_n = _smart_top_n(dimension)
                    if len(valid_measures) > 1:
                        # Multi-series grouped bar chart
                        _agg_frames: dict = {}
                        for m in valid_measures[:4]:
                            m_agg = _get_role_agg(m)
                            _agg_frames[m] = _smart_groupby(
                                _col_series(dimension), _numeric_series(m), m_agg, top_n=999
                            )
                        # Pick top-N dimensions by combined absolute total
                        total_df = pd.DataFrame(_agg_frames).abs().fillna(0)
                        top_dims = list(total_df.sum(axis=1).nlargest(top_n).index)
                        labels = [str(l) for l in top_dims]
                        bar_datasets = [
                            {
                                "label": _humanize_col(m),
                                "data": [round(float(_agg_frames[m].get(d, 0)), 2) for d in top_dims],
                            }
                            for m in valid_measures[:4] if m in _agg_frames
                        ]
                        config = _multi_bar_config(labels, bar_datasets, palette,
                                                   x_label=_humanize_col(dimension),
                                                   y_label=_humanize_col(valid_measures[0]))
                        config["layout"] = {"size": size}
                        all_vals = [v for ds in bar_datasets for v in ds["data"]]
                        _flag_large_numbers(config, all_vals)
                        if not ai_insight and labels and bar_datasets:
                            try:
                                ai_insight, _ = ai_analyze_chart("bar", labels, bar_datasets[0]["data"], title)
                            except Exception:
                                pass
                    else:
                        m = valid_measures[0]
                        agg_m = _get_role_agg(m)
                        top = _smart_groupby(_col_series(dimension), _numeric_series(m), agg_m, top_n)
                        labels = [str(l) for l in top.index]
                        values = [round(float(v), 2) for v in top.values]
                        config = _bar_config(labels, values, _humanize_col(m), palette,
                                             x_label=_humanize_col(dimension), y_label=_humanize_col(m))
                        config["layout"] = {"size": size}
                        _flag_large_numbers(config, values)
                        if not ai_insight and labels and values:
                            try:
                                ai_insight, _ = ai_analyze_chart("bar", labels, values, title)
                            except Exception:
                                pass

            # ── Horizontal bar ────────────────────────────────────────────────
            elif chart_type == "hbar" and dimension and dimension in df.columns:
                valid_measures = [m for m in measures if m and m in df.columns]
                if not valid_measures and measure and measure in df.columns:
                    valid_measures = [measure]
                if valid_measures:
                    m = valid_measures[0]
                    agg_m = _get_role_agg(m)
                    top_n = _smart_top_n(dimension)
                    top = _smart_groupby(_col_series(dimension), _numeric_series(m), agg_m, top_n)
                    labels = [str(l) for l in top.index]
                    values = [round(float(v), 2) for v in top.values]
                    config = _hbar_config(labels, values, _humanize_col(m), palette,
                                          x_label=_humanize_col(m), y_label=_humanize_col(dimension))
                    config["layout"] = {"size": size}
                    _flag_large_numbers(config, values)
                    if not ai_insight and labels and values:
                        try:
                            ai_insight, _ = ai_analyze_chart("hbar", labels, values, title)
                        except Exception:
                            pass

            # ── Line chart ────────────────────────────────────────────────────
            elif chart_type == "line" and dimension and dimension in df.columns:
                valid_measures = [m for m in measures if m and m in df.columns]
                if not valid_measures and measure and measure in df.columns:
                    valid_measures = [measure]
                if valid_measures:
                    dt_series = _to_datetime_series(_col_series(dimension))
                    is_temporal = dt_series.notna().mean() >= 0.6
                    if is_temporal:
                        period = _get_date_granularity(dt_series)
                        x_label = _GRAN_LABELS.get(period, "Period")
                    else:
                        period = None
                        x_label = _humanize_col(dimension)

                    if len(valid_measures) > 1:
                        # Multi-series line chart
                        if is_temporal:
                            _tmp = pd.DataFrame({"_dim": dt_series})
                        else:
                            _tmp = pd.DataFrame({"_dim": _col_series(dimension)})
                        for m in valid_measures[:4]:
                            _tmp[m] = _numeric_series(m)
                        _tmp = _tmp.dropna(subset=["_dim"])
                        if is_temporal:
                            _tmp = _tmp.sort_values("_dim")
                            _grouped = _tmp.groupby(_tmp["_dim"].dt.to_period(period))
                        else:
                            _grouped = _tmp.groupby("_dim")
                        all_line_labels = None
                        line_datasets = []
                        for m in valid_measures[:4]:
                            m_agg = _get_role_agg(m)
                            if m_agg == "avg":
                                _s = _grouped[m].mean()
                            elif m_agg == "count":
                                _s = _grouped[m].count()
                            else:
                                _s = _grouped[m].sum()
                            if all_line_labels is None:
                                all_line_labels = [str(p) for p in _s.index]
                            line_datasets.append({
                                "label": _humanize_col(m),
                                "data": [round(float(v), 2) for v in _s.values],
                            })
                        labels = all_line_labels or []
                        config = _multi_line_config(labels, line_datasets, palette,
                                                     x_label=x_label,
                                                     y_label=_humanize_col(valid_measures[0]))
                        config["layout"] = {"size": size}
                        all_vals = [v for ds in line_datasets for v in ds["data"]]
                        _flag_large_numbers(config, all_vals)
                        if not ai_insight and labels and line_datasets:
                            try:
                                ai_insight, _ = ai_analyze_chart("line", labels, line_datasets[0]["data"], title)
                            except Exception:
                                pass
                    else:
                        m = valid_measures[0]
                        m_agg = _get_role_agg(m)
                        _tmp = pd.DataFrame({"_dim": dt_series if is_temporal else _col_series(dimension),
                                              "_val": _numeric_series(m)})
                        _tmp = _tmp.dropna(subset=["_dim"])
                        if is_temporal:
                            _tmp = _tmp.sort_values("_dim")
                            if m_agg == "avg":
                                trend_data = _tmp.groupby(_tmp["_dim"].dt.to_period(period))["_val"].mean()
                            else:
                                trend_data = _tmp.groupby(_tmp["_dim"].dt.to_period(period))["_val"].sum()
                        else:
                            trend_data = _tmp.groupby("_dim")["_val"].sum()
                        labels = [str(p) for p in trend_data.index]
                        values = [round(float(v), 2) for v in trend_data.values]
                        config = _line_config(labels, values, _humanize_col(m), palette,
                                              x_label=x_label, y_label=_humanize_col(m))
                        config["layout"] = {"size": size}
                        _flag_large_numbers(config, values)
                        if not ai_insight and labels and values:
                            try:
                                ai_insight, _ = ai_analyze_chart("line", labels, values, title)
                            except Exception:
                                pass

            # ── Area chart ────────────────────────────────────────────────────
            elif chart_type == "area" and dimension and dimension in df.columns:
                valid_measures = [m for m in measures if m and m in df.columns]
                if not valid_measures and measure and measure in df.columns:
                    valid_measures = [measure]
                if valid_measures:
                    m = valid_measures[0]
                    m_agg = _get_role_agg(m)
                    dt_series = _to_datetime_series(_col_series(dimension))
                    is_temporal = dt_series.notna().mean() >= 0.6
                    if is_temporal:
                        period = _get_date_granularity(dt_series)
                        x_label = _GRAN_LABELS.get(period, "Period")
                        _tmp = pd.DataFrame({"_dim": dt_series, "_val": _numeric_series(m)})
                        _tmp = _tmp.dropna(subset=["_dim"]).sort_values("_dim")
                        if m_agg == "avg":
                            trend_data = _tmp.groupby(_tmp["_dim"].dt.to_period(period))["_val"].mean()
                        else:
                            trend_data = _tmp.groupby(_tmp["_dim"].dt.to_period(period))["_val"].sum()
                    else:
                        x_label = _humanize_col(dimension)
                        _tmp = pd.DataFrame({"_dim": _col_series(dimension), "_val": _numeric_series(m)})
                        trend_data = _tmp.groupby("_dim")["_val"].sum()
                    labels = [str(p) for p in trend_data.index]
                    values = [round(float(v), 2) for v in trend_data.values]
                    config = _area_config(labels, values, _humanize_col(m), palette,
                                          x_label=x_label, y_label=_humanize_col(m))
                    config["layout"] = {"size": size}
                    _flag_large_numbers(config, values)
                    if not ai_insight and labels and values:
                        try:
                            ai_insight, _ = ai_analyze_chart("area", labels, values, title)
                        except Exception:
                            pass

            # ── Pie / Doughnut ────────────────────────────────────────────────
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
                    try:
                        ai_insight, _ = ai_analyze_chart(chart_type, labels, values, title)
                    except Exception:
                        pass

            # ── Scatter ───────────────────────────────────────────────────────
            elif chart_type == "scatter":
                # Try x_measure/y_measure first, then fall back to first two numeric columns
                rx = x_measure if x_measure and x_measure in df.columns else None
                ry = y_measure if y_measure and y_measure in df.columns else None
                if not rx or not ry:
                    nums = [c for c in profile.numeric_columns if c in df.columns]
                    if len(nums) >= 2:
                        rx = rx or nums[0]
                        ry = ry or (nums[1] if nums[1] != rx else nums[2] if len(nums) > 2 else None)
                if rx and ry and rx in df.columns and ry in df.columns:
                    tmp = df[[rx, ry]].dropna().head(500)
                    x_vals = [round(float(v), 4) for v in tmp[rx]]
                    y_vals = [round(float(v), 4) for v in tmp[ry]]
                    from apps.datasets.services import _scatter_config as _sc
                    scatter_title = f"{_humanize_col(rx)} vs {_humanize_col(ry)}"
                    config = _sc(x_vals, y_vals, _humanize_col(rx), _humanize_col(ry), palette, scatter_title)
                    config["layout"] = {"size": size}
                    if not ai_insight:
                        try:
                            ai_insight, _ = ai_analyze_chart("scatter", x_vals[:40], y_vals[:40], title)
                        except Exception:
                            pass

            # ── Radar ─────────────────────────────────────────────────────────
            elif chart_type == "radar" and dimension and measure and dimension in df.columns and measure in df.columns:
                top = _numeric_series(measure).groupby(_col_series(dimension)).sum().nlargest(8)
                labels = [str(l) for l in top.index]
                values = [round(float(v), 2) for v in top.values]
                config = _radar_config(labels, values, _humanize_col(measure), palette)
                config["layout"] = {"size": size}
                if not ai_insight and labels and values:
                    try:
                        ai_insight, _ = ai_analyze_chart("radar", labels, values, title)
                    except Exception:
                        pass

            # ── Bubble chart ──────────────────────────────────────────────────
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
                    _seen: set = set()
                    _bcols = [c for c in [rx, ry, rr] if c and c in df.columns
                              and not (_seen.__contains__(c) or _seen.add(c))]
                    tmp = df[_bcols].dropna().head(200)
                    x_raw = pd.to_numeric(tmp[rx], errors="coerce").tolist()
                    y_raw = pd.to_numeric(tmp[ry], errors="coerce").tolist()
                    if rr and rr in tmp.columns:
                        r_raw = pd.to_numeric(tmp[rr], errors="coerce").tolist()
                        r_min = min(r_raw) if r_raw else 0
                        r_max = max(r_raw) if r_raw else 1
                        r_range = max(r_max - r_min, 1)
                        r_norm = [max(4, round((v - r_min) / r_range * 30 + 4, 1)) for v in r_raw]
                    else:
                        r_norm = [8] * len(x_raw)
                    data_pts = [{"x": round(float(x), 4), "y": round(float(y), 4), "r": r}
                                for x, y, r in zip(x_raw, y_raw, r_norm)]
                    config = _bubble_config(data_pts, title, palette,
                                           x_label=_humanize_col(rx), y_label=_humanize_col(ry))
                    config["layout"] = {"size": size}
                    if not ai_insight:
                        ai_insight = (
                            f"Bubble chart visualizing {_humanize_col(rx)} vs {_humanize_col(ry)}"
                            + (f" with bubble size representing {_humanize_col(rr)}" if rr else "")
                            + f" across {len(data_pts)} data points. "
                            f"Larger bubbles and clusters indicate high-value segments worth prioritizing."
                        )

            # ── Polar Area ────────────────────────────────────────────────────
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
                    try:
                        ai_insight, _ = ai_analyze_chart("polararea", labels, values, title)
                    except Exception:
                        pass

            # ── Mixed (bar + line) ────────────────────────────────────────────
            elif chart_type == "mixed" and dimension and dimension in df.columns:
                from apps.datasets.services import _mixed_config
                bar_measures = [m for m in measures[:2] if m and m in df.columns and m != dimension]
                line_measures = [m for m in measures[2:4] if m and m in df.columns and m != dimension]
                if not bar_measures and measure and measure in df.columns and measure != dimension:
                    bar_measures = [measure]
                # Need at least 1 bar measure
                if bar_measures:
                    # Keep only truly numeric measures and remove duplicates while preserving order.
                    all_mix_cols: list[str] = []
                    for candidate in bar_measures + line_measures:
                        if candidate in all_mix_cols:
                            continue
                        numeric_candidate = _numeric_series(candidate)
                        if numeric_candidate.notna().sum() > 0:
                            all_mix_cols.append(candidate)
                    if not all_mix_cols:
                        continue
                    bar_measures = [m for m in bar_measures if m in all_mix_cols]
                    line_measures = [m for m in line_measures if m in all_mix_cols and m not in bar_measures]
                    if not bar_measures:
                        bar_measures = [all_mix_cols[0]]
                    _mix_tmp = pd.DataFrame({"_dim": _col_series(dimension)})
                    for _mc in all_mix_cols:
                        _mix_tmp[_mc] = _numeric_series(_mc)
                    grouped = _mix_tmp.groupby("_dim")[all_mix_cols].sum().head(12)
                    labels = [str(l) for l in grouped.index]
                    def _series_from_group(col_name: str):
                        series_or_df = grouped[col_name]
                        if isinstance(series_or_df, pd.DataFrame):
                            return series_or_df.iloc[:, 0]
                        return series_or_df
                    bar_ds = [{"label": _humanize_col(m), "data": [round(float(v), 2) for v in _series_from_group(m)]} for m in bar_measures]
                    line_ds = [{"label": _humanize_col(m), "data": [round(float(v), 2) for v in _series_from_group(m)]} for m in line_measures]
                    config = _mixed_config(labels, bar_ds, line_ds, palette,
                                          x_label=_humanize_col(dimension),
                                          y_label=_humanize_col(bar_measures[0]) if bar_measures else "")
                    config["layout"] = {"size": size}
                    if not ai_insight:
                        ai_insight = (
                            f"Dual-axis chart showing {', '.join(_humanize_col(m) for m in bar_measures)} "
                            f"(bars) vs {', '.join(_humanize_col(m) for m in line_measures) if line_measures else 'trend'} "
                            f"(line) across {len(labels)} {_humanize_col(dimension)} segments. "
                            f"Use to identify volume-rate relationships and outlier segments."
                        )

            # ── Funnel ────────────────────────────────────────────────────────
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
                        f"Funnel shows {len(labels)} stages from '{labels[0]}' ({values[0]:,.0f}) "
                        f"to '{labels[-1]}' ({values[-1]:,.0f}) — a {drop}% drop-off overall. "
                        f"Largest conversion gap: identify the stage with steepest fall for optimization."
                    )

            # ── Gauge ─────────────────────────────────────────────────────────
            elif chart_type == "gauge" and measure and measure in df.columns:
                from apps.datasets.services import _gauge_config
                col_data = pd.to_numeric(df[measure], errors="coerce").dropna()
                if len(col_data) > 0:
                    val = float(col_data.mean())  # show average as gauge needle
                    min_v = float(col_data.min())
                    max_v = float(col_data.max())
                    config = _gauge_config(val, min_v, max_v, _humanize_col(measure), palette)
                    config["layout"] = {"size": size}
                    pct = round((val - min_v) / max(max_v - min_v, 1) * 100, 1)
                    if not ai_insight:
                        ai_insight = (
                            f"Average {_humanize_col(measure)} is {val:,.2f} — "
                            f"at {pct}% of the range ({min_v:,.2f}–{max_v:,.2f}). "
                            f"Use target lines to assess performance against benchmarks."
                        )

            # ── Waterfall ─────────────────────────────────────────────────────
            elif chart_type == "waterfall" and dimension and measure and dimension in df.columns and measure in df.columns:
                from apps.datasets.services import _waterfall_config
                grouped = _numeric_series(measure).groupby(_col_series(dimension)).sum().head(10)
                labels = [str(l) for l in grouped.index]
                values = [round(float(v), 2) for v in grouped.values]
                config = _waterfall_config(labels, values, _humanize_col(measure), palette,
                                           x_label=_humanize_col(dimension), y_label=_humanize_col(measure))
                config["layout"] = {"size": size}
                if not ai_insight and labels and values:
                    pos = sum(v for v in values if v > 0)
                    neg = sum(v for v in values if v < 0)
                    ai_insight = (
                        f"Waterfall breakdown: {_humanize_col(measure)} totals "
                        f"+{pos:,.0f} gains vs {neg:,.0f} deductions across {len(labels)} categories. "
                        f"Identify the largest contributors and detractors for budget or P&L optimization."
                    )

            # ── Table ─────────────────────────────────────────────────────────
            elif chart_type == "table":
                all_candidates = ([dimension] if dimension else []) + measures
                cols = [c for c in all_candidates if c and c in df.columns]
                if not cols:
                    # Smart fallback: date cols first, then categorical, then numeric
                    date_like = [
                        c for c in df.columns
                        if any(k in str(c).lower() for k in ["date", "month", "year", "period", "quarter"])
                        and c in df.columns
                    ]
                    cols = (
                        date_like[:1]
                        + [c for c in profile.categorical_columns[:2] if c in df.columns]
                        + [c for c in profile.numeric_columns[:4] if c in df.columns]
                    )[:6]
                if not cols:
                    cols = [str(c) for c in df.columns[:6]]
                # Sort by the first numeric column descending for top-record view
                sort_col = next(
                    (c for c in cols if c in profile.numeric_columns and c in df.columns), None
                )
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
                    human_cols = [_humanize_col(c) for c in cols[:4]]
                    ai_insight = (
                        f"Showing top {len(rows)} records"
                        + (f" sorted by {_humanize_col(sort_col)} (descending)" if sort_col else "")
                        + f" across {len(cols)} columns: "
                        + f"{', '.join(human_cols[:3])}{'...' if len(cols) > 3 else ''}. "
                        f"Use filters to drill into specific segments and identify outliers."
                    )

        except Exception as exc:
            logger.warning("Widget spec build failed for '%s' (%s): %s", title, chart_type, exc)
            continue

        if not config:
            logger.debug("Skipping '%s' (%s) — no config produced (column resolution failed)", title, chart_type)
            continue

        # Attach AI insight
        if ai_insight:
            config["ai_insight"] = ai_insight[:600]

        # Attach builder metadata for filter rebuilding
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
