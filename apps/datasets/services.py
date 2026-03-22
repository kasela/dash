from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class ParsedPreview:
    headers: list[str]
    rows: list[dict[str, object]]
    shape: tuple[int, int]
    dataframe: pd.DataFrame


@dataclass
class ProfileSummary:
    total_rows: int
    total_columns: int
    duplicate_rows: int
    missing_cells: int
    numeric_columns: list[str]
    categorical_columns: list[str]
    suggested_dimensions: list[str]
    suggested_measures: list[str]


@dataclass
class WidgetSuggestion:
    title: str
    chart_type: str  # "bar", "line", "pie", "kpi"
    description: str


def parse_uploaded_file(file_obj) -> ParsedPreview:
    name = file_obj.name.lower()
    if name.endswith(".csv"):
        df = pd.read_csv(file_obj)
    elif name.endswith((".xlsx", ".xlsm")):
        df = pd.read_excel(file_obj)
    elif name.endswith(".json"):
        df = pd.read_json(file_obj)
    else:
        raise ValueError("Unsupported file type")

    sample_df = df.head(100)
    records = sample_df.where(pd.notnull(sample_df), None).to_dict(orient="records")
    return ParsedPreview(
        headers=[str(h) for h in sample_df.columns],
        rows=records,
        shape=df.shape,
        dataframe=df,
    )


def infer_column_kind(series: pd.Series) -> str:
    lower_name = str(series.name).lower()
    if "date" in lower_name or "month" in lower_name or "year" in lower_name:
        return "date"
    if "id" in lower_name or "code" in lower_name:
        return "id"
    if pd.api.types.is_numeric_dtype(series):
        return "measure"
    if series.nunique(dropna=True) <= 1:
        return "unknown"
    return "dimension"


def build_profile_summary(df: pd.DataFrame) -> ProfileSummary:
    numeric_columns = [str(c) for c in df.select_dtypes(include=["number"]).columns]
    categorical_columns = [str(c) for c in df.select_dtypes(exclude=["number", "datetime"]).columns]

    suggested_dimensions = categorical_columns[:6]
    suggested_measures = numeric_columns[:6]

    return ProfileSummary(
        total_rows=int(df.shape[0]),
        total_columns=int(df.shape[1]),
        duplicate_rows=int(df.duplicated().sum()),
        missing_cells=int(df.isna().sum().sum()),
        numeric_columns=numeric_columns,
        categorical_columns=categorical_columns,
        suggested_dimensions=suggested_dimensions,
        suggested_measures=suggested_measures,
    )


def build_widget_suggestions(profile: ProfileSummary) -> list[WidgetSuggestion]:
    suggestions: list[WidgetSuggestion] = []

    if profile.suggested_measures and profile.suggested_dimensions:
        dim = profile.suggested_dimensions[0]
        measure = profile.suggested_measures[0]
        suggestions.append(WidgetSuggestion(
            title=f"{measure} by {dim}",
            chart_type="bar",
            description=f"Compare {measure} across {dim} categories",
        ))

    if profile.suggested_measures and profile.suggested_dimensions:
        dim = profile.suggested_dimensions[0]
        measure = profile.suggested_measures[0]
        suggestions.append(WidgetSuggestion(
            title=f"Distribution of {dim}",
            chart_type="pie",
            description=f"Show proportion breakdown of {dim} values",
        ))

    date_like_dims = [d for d in profile.suggested_dimensions if any(k in d.lower() for k in ["date", "month", "year", "period", "quarter"])]
    if date_like_dims and profile.suggested_measures:
        suggestions.append(WidgetSuggestion(
            title=f"{profile.suggested_measures[0]} over time",
            chart_type="line",
            description=f"Track trend of {profile.suggested_measures[0]} by {date_like_dims[0]}",
        ))

    if profile.suggested_measures:
        measure = profile.suggested_measures[0]
        suggestions.append(WidgetSuggestion(
            title=f"Total {measure}",
            chart_type="kpi",
            description=f"Sum of all {measure} values as a key metric",
        ))

    if profile.duplicate_rows > 0:
        suggestions.append(WidgetSuggestion(
            title="Duplicate records",
            chart_type="kpi",
            description=f"{profile.duplicate_rows} duplicate rows detected in this dataset",
        ))

    if not suggestions:
        suggestions.append(WidgetSuggestion(
            title="Overview KPI dashboard",
            chart_type="kpi",
            description="Summary of key metrics from your dataset",
        ))

    return suggestions[:6]


# ── External URL import ─────────────────────────────────────────────────────────

def detect_external_source_type(url: str) -> str:
    """Return ExternalDataSource.SourceType value for a given URL."""
    lower = url.lower()
    if "docs.google.com/spreadsheets" in lower:
        return "google_sheets"
    if (
        "onedrive.live.com" in lower
        or "sharepoint.com" in lower
        or "1drv.ms" in lower
        or "excel" in lower
    ):
        return "excel_online"
    return "direct_url"


def build_csv_export_url(url: str) -> str:
    """Convert a Google Sheets share URL to a CSV export URL; pass others through."""
    import re

    # Google Sheets: extract spreadsheet ID and optional gid
    gsheets = re.match(
        r"https://docs\.google\.com/spreadsheets/d/([^/?#]+)(?:[^?#]*)?(?:\?[^#]*)?(?:#gid=(\d+))?",
        url,
    )
    if gsheets:
        sheet_id = gsheets.group(1)
        gid = gsheets.group(2) or "0"
        return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"

    # OneDrive personal share URL → direct download
    # https://onedrive.live.com/edit?resid=X&authkey=Y  → https://onedrive.live.com/download?resid=X&authkey=Y
    if "onedrive.live.com" in url.lower():
        return url.replace("/edit?", "/download?").replace("/view?", "/download?")

    return url


def fetch_from_url(url: str) -> "ParsedPreview":
    """Fetch tabular data from a public URL (Google Sheets, Excel Online, direct CSV/XLSX)."""
    import io
    import urllib.request

    export_url = build_csv_export_url(url)

    req = urllib.request.Request(
        export_url,
        headers={"User-Agent": "Mozilla/5.0 DashAI-Importer/1.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()

    lower = export_url.lower()
    if "format=csv" in lower or lower.endswith(".csv"):
        df = pd.read_csv(io.BytesIO(raw))
    elif any(x in lower for x in [".xlsx", ".xlsm", "format=xlsx"]):
        df = pd.read_excel(io.BytesIO(raw))
    else:
        try:
            df = pd.read_csv(io.BytesIO(raw))
        except Exception:
            df = pd.read_excel(io.BytesIO(raw))

    sample_df = df.head(100)
    records = sample_df.where(pd.notnull(sample_df), None).to_dict(orient="records")
    return ParsedPreview(
        headers=[str(h) for h in sample_df.columns],
        rows=records,
        shape=df.shape,
        dataframe=df,
    )


# ── Chart palettes ─────────────────────────────────────────────────────────────

PALETTES = {
    "indigo":  ["#6366f1", "#8b5cf6", "#a78bfa", "#c4b5fd", "#818cf8", "#4f46e5", "#7c3aed", "#9061f9", "#a855f7", "#d946ef"],
    "blue":    ["#3b82f6", "#60a5fa", "#93c5fd", "#1d4ed8", "#2563eb", "#0ea5e9", "#38bdf8", "#7dd3fc", "#1e40af", "#172554"],
    "emerald": ["#10b981", "#34d399", "#6ee7b7", "#059669", "#065f46", "#14b8a6", "#2dd4bf", "#5eead4", "#0f766e", "#134e4a"],
    "rose":    ["#f43f5e", "#fb7185", "#fda4af", "#e11d48", "#9f1239", "#f97316", "#fb923c", "#fdba74", "#ea580c", "#7c2d12"],
    "amber":   ["#f59e0b", "#fbbf24", "#fcd34d", "#d97706", "#92400e", "#eab308", "#facc15", "#fde047", "#ca8a04", "#713f12"],
    "slate":   ["#475569", "#64748b", "#94a3b8", "#1e293b", "#334155", "#6b7280", "#9ca3af", "#d1d5db", "#374151", "#111827"],
    "vibrant": ["#6366f1", "#10b981", "#f59e0b", "#f43f5e", "#3b82f6", "#8b5cf6", "#14b8a6", "#fb923c", "#84cc16", "#ec4899"],
    "ocean":   ["#0ea5e9", "#06b6d4", "#22d3ee", "#0284c7", "#0369a1", "#38bdf8", "#67e8f9", "#0891b2", "#155e75", "#164e63"],
    "sunset":  ["#f97316", "#ef4444", "#ec4899", "#a855f7", "#f59e0b", "#fb923c", "#f43f5e", "#d946ef", "#e11d48", "#9333ea"],
    "mono":    ["#1e293b", "#334155", "#475569", "#64748b", "#94a3b8", "#cbd5e1", "#e2e8f0", "#334155", "#0f172a", "#475569"],
    "neon":    ["#22d3ee", "#a3e635", "#fb923c", "#f472b6", "#c084fc", "#34d399", "#fbbf24", "#f87171", "#60a5fa", "#4ade80"],
}

DEFAULT_PALETTE = PALETTES["indigo"]

_MULTI_COLORS = [
    "#6366f1", "#10b981", "#f59e0b", "#f43f5e", "#3b82f6", "#8b5cf6",
    "#14b8a6", "#fb923c", "#e11d48", "#2563eb",
]


_TOOLTIP_OPTS = {
    "backgroundColor": "rgba(15,23,42,0.92)",
    "titleColor": "#f8fafc",
    "bodyColor": "#cbd5e1",
    "borderColor": "rgba(99,102,241,0.3)",
    "borderWidth": 1,
    "padding": 10,
    "cornerRadius": 8,
    "callbacks": {
        "label": "function(ctx){var v=ctx.parsed.y??ctx.parsed;if(typeof v==='number')return ' '+v.toLocaleString();return ' '+v;}"
    },
}

_ANIMATION_OPTS = {
    "duration": 600,
    "easing": "easeInOutQuart",
}


def _scale_opts(x_label: str = "", y_label: str = "") -> dict:
    x = {
        "grid": {"display": False},
        "ticks": {"color": "#94a3b8", "font": {"size": 11}},
    }
    y = {
        "grid": {"color": "rgba(0,0,0,0.05)", "drawBorder": False},
        "ticks": {"color": "#94a3b8", "font": {"size": 11}},
    }
    if x_label:
        x["title"] = {"display": True, "text": x_label, "color": "#64748b", "font": {"size": 12, "weight": "500"}}
    if y_label:
        y["title"] = {"display": True, "text": y_label, "color": "#64748b", "font": {"size": 12, "weight": "500"}}
    return {"x": x, "y": y}


def _resolve_palette(palette_name: str, n: int) -> list:
    colors = PALETTES.get(palette_name, DEFAULT_PALETTE)
    # cycle if needed
    return [colors[i % len(colors)] for i in range(n)]


def _bar_config(labels: list, values: list, label: str, palette: str = "indigo",
                x_label: str = "", y_label: str = "") -> dict:
    colors = _resolve_palette(palette, len(labels))
    return {
        "type": "bar",
        "data": {
            "labels": labels,
            "datasets": [{
                "label": label,
                "data": values,
                "backgroundColor": colors,
                "borderRadius": 6,
                "borderSkipped": False,
                "hoverBackgroundColor": [c + "cc" for c in colors],
            }],
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "animation": _ANIMATION_OPTS,
            "plugins": {
                "legend": {"display": False},
                "tooltip": _TOOLTIP_OPTS,
            },
            "scales": _scale_opts(x_label, y_label),
        },
    }


def _multi_bar_config(labels: list, datasets: list[dict], palette: str = "indigo",
                      x_label: str = "", y_label: str = "") -> dict:
    """Multi-series bar chart. datasets = [{"label": str, "data": list}, ...]"""
    chart_datasets = []
    for i, ds in enumerate(datasets):
        color = _MULTI_COLORS[i % len(_MULTI_COLORS)]
        chart_datasets.append({
            "label": ds["label"],
            "data": ds["data"],
            "backgroundColor": color,
            "borderRadius": 4,
            "borderSkipped": False,
            "hoverBackgroundColor": color + "cc",
        })
    return {
        "type": "bar",
        "data": {"labels": labels, "datasets": chart_datasets},
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "animation": _ANIMATION_OPTS,
            "plugins": {
                "legend": {"display": True, "position": "top", "labels": {"color": "#475569", "font": {"size": 12}}},
                "tooltip": _TOOLTIP_OPTS,
            },
            "scales": _scale_opts(x_label, y_label),
        },
    }


def _line_config(labels: list, values: list, label: str, palette: str = "indigo",
                 x_label: str = "", y_label: str = "") -> dict:
    colors = _resolve_palette(palette, 1)
    border = colors[0]
    return {
        "type": "line",
        "data": {
            "labels": labels,
            "datasets": [{
                "label": label,
                "data": values,
                "borderColor": border,
                "backgroundColor": border + "1a",
                "tension": 0.4,
                "fill": False,
                "pointRadius": 4,
                "pointHoverRadius": 6,
                "pointBackgroundColor": border,
                "pointBorderColor": "#ffffff",
                "pointBorderWidth": 2,
                "borderWidth": 2.5,
            }],
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "animation": _ANIMATION_OPTS,
            "plugins": {
                "legend": {"display": False},
                "tooltip": _TOOLTIP_OPTS,
            },
            "scales": _scale_opts(x_label, y_label),
        },
    }


def _multi_line_config(labels: list, datasets: list[dict], palette: str = "indigo",
                       x_label: str = "", y_label: str = "") -> dict:
    chart_datasets = []
    for i, ds in enumerate(datasets):
        color = _MULTI_COLORS[i % len(_MULTI_COLORS)]
        chart_datasets.append({
            "label": ds["label"],
            "data": ds["data"],
            "borderColor": color,
            "backgroundColor": color + "1a",
            "tension": 0.4,
            "fill": False,
            "pointRadius": 4,
            "pointHoverRadius": 6,
            "pointBackgroundColor": color,
            "pointBorderColor": "#ffffff",
            "pointBorderWidth": 2,
            "borderWidth": 2.5,
        })
    return {
        "type": "line",
        "data": {"labels": labels, "datasets": chart_datasets},
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "animation": _ANIMATION_OPTS,
            "plugins": {
                "legend": {"display": True, "position": "top", "labels": {"color": "#475569", "font": {"size": 12}}},
                "tooltip": _TOOLTIP_OPTS,
            },
            "scales": _scale_opts(x_label, y_label),
        },
    }


def _area_config(labels: list, values: list, label: str, palette: str = "indigo",
                 x_label: str = "", y_label: str = "") -> dict:
    colors = _resolve_palette(palette, 1)
    border = colors[0]
    return {
        "type": "line",
        "data": {
            "labels": labels,
            "datasets": [{
                "label": label,
                "data": values,
                "borderColor": border,
                "backgroundColor": border + "28",
                "tension": 0.4,
                "fill": True,
                "pointRadius": 4,
                "pointHoverRadius": 6,
                "pointBackgroundColor": border,
                "pointBorderColor": "#ffffff",
                "pointBorderWidth": 2,
                "borderWidth": 2.5,
            }],
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "animation": _ANIMATION_OPTS,
            "plugins": {
                "legend": {"display": False},
                "tooltip": _TOOLTIP_OPTS,
            },
            "scales": _scale_opts(x_label, y_label),
        },
    }


def _pie_config(labels: list, values: list, palette: str = "indigo") -> dict:
    colors = _resolve_palette(palette, len(labels))
    return {
        "type": "pie",
        "data": {
            "labels": labels,
            "datasets": [{
                "data": values,
                "backgroundColor": colors,
                "hoverOffset": 10,
                "borderWidth": 2,
                "borderColor": "#ffffff",
            }],
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "animation": _ANIMATION_OPTS,
            "plugins": {
                "legend": {"position": "bottom", "labels": {"font": {"size": 11}, "color": "#64748b", "padding": 16}},
                "tooltip": _TOOLTIP_OPTS,
            },
        },
    }


def _doughnut_config(labels: list, values: list, palette: str = "indigo") -> dict:
    colors = _resolve_palette(palette, len(labels))
    return {
        "type": "doughnut",
        "data": {
            "labels": labels,
            "datasets": [{
                "data": values,
                "backgroundColor": colors,
                "hoverOffset": 10,
                "borderWidth": 3,
                "borderColor": "#ffffff",
            }],
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "cutout": "68%",
            "animation": _ANIMATION_OPTS,
            "plugins": {
                "legend": {"position": "bottom", "labels": {"font": {"size": 11}, "color": "#64748b", "padding": 16}},
                "tooltip": _TOOLTIP_OPTS,
            },
        },
    }


def _hbar_config(labels: list, values: list, label: str, palette: str = "indigo",
                 x_label: str = "", y_label: str = "") -> dict:
    colors = _resolve_palette(palette, len(labels))
    return {
        "type": "bar",
        "data": {
            "labels": labels,
            "datasets": [{
                "label": label,
                "data": values,
                "backgroundColor": colors,
                "borderRadius": 4,
                "borderSkipped": False,
                "hoverBackgroundColor": [c + "cc" for c in colors],
            }],
        },
        "options": {
            "indexAxis": "y",
            "responsive": True,
            "maintainAspectRatio": False,
            "animation": _ANIMATION_OPTS,
            "plugins": {
                "legend": {"display": False},
                "tooltip": _TOOLTIP_OPTS,
            },
            "scales": _scale_opts(x_label, y_label),
        },
    }


def _scatter_config(x_values: list, y_values: list, x_label: str = "", y_label: str = "",
                    palette: str = "indigo", label: str = "Data") -> dict:
    colors = _resolve_palette(palette, 1)
    points = [{"x": x, "y": y} for x, y in zip(x_values, y_values)]
    return {
        "type": "scatter",
        "data": {
            "datasets": [{
                "label": label,
                "data": points,
                "backgroundColor": colors[0] + "88",
                "borderColor": colors[0],
                "borderWidth": 1.5,
                "pointRadius": 5,
                "pointHoverRadius": 7,
            }],
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "animation": _ANIMATION_OPTS,
            "plugins": {
                "legend": {"display": False},
                "tooltip": _TOOLTIP_OPTS,
            },
            "scales": _scale_opts(x_label, y_label),
        },
    }


def _radar_config(labels: list, values: list, label: str, palette: str = "indigo") -> dict:
    colors = _resolve_palette(palette, 1)
    border = colors[0]
    return {
        "type": "radar",
        "data": {
            "labels": labels,
            "datasets": [{
                "label": label,
                "data": values,
                "borderColor": border,
                "backgroundColor": border + "28",
                "pointRadius": 4,
                "pointHoverRadius": 6,
                "pointBackgroundColor": border,
                "pointBorderColor": "#ffffff",
                "pointBorderWidth": 2,
                "borderWidth": 2,
            }],
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "animation": _ANIMATION_OPTS,
            "plugins": {
                "legend": {"display": False},
                "tooltip": _TOOLTIP_OPTS,
            },
            "scales": {
                "r": {
                    "ticks": {"color": "#94a3b8", "backdropColor": "transparent", "font": {"size": 10}},
                    "grid": {"color": "rgba(0,0,0,0.08)"},
                    "pointLabels": {"color": "#64748b", "font": {"size": 11}},
                }
            },
        },
    }


def _bubble_config(data_points: list[dict], label: str, palette: str = "indigo",
                   x_label: str = "", y_label: str = "") -> dict:
    """Bubble chart: data_points = [{"x": ..., "y": ..., "r": ...}, ...]"""
    colors = _resolve_palette(palette, 1)
    color = colors[0]
    return {
        "type": "bubble",
        "data": {
            "datasets": [{
                "label": label,
                "data": data_points,
                "backgroundColor": color + "88",
                "borderColor": color,
                "borderWidth": 1.5,
                "hoverBackgroundColor": color + "bb",
            }],
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "animation": _ANIMATION_OPTS,
            "plugins": {
                "legend": {"display": False},
                "tooltip": _TOOLTIP_OPTS,
            },
            "scales": _scale_opts(x_label, y_label),
        },
    }


def _polararea_config(labels: list, values: list, palette: str = "indigo") -> dict:
    """Polar area chart – like pie but segment radius encodes value."""
    colors = _resolve_palette(palette, len(labels))
    return {
        "type": "polarArea",
        "data": {
            "labels": labels,
            "datasets": [{
                "data": values,
                "backgroundColor": [c + "cc" for c in colors],
                "borderColor": colors,
                "borderWidth": 2,
                "hoverBackgroundColor": colors,
            }],
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "animation": _ANIMATION_OPTS,
            "plugins": {
                "legend": {"position": "bottom", "labels": {"font": {"size": 11}, "color": "#64748b", "padding": 14}},
                "tooltip": _TOOLTIP_OPTS,
            },
            "scales": {
                "r": {
                    "ticks": {"color": "#94a3b8", "backdropColor": "transparent", "font": {"size": 10}},
                    "grid": {"color": "rgba(0,0,0,0.07)"},
                }
            },
        },
    }


def _mixed_config(labels: list, bar_datasets: list[dict], line_datasets: list[dict],
                  palette: str = "indigo", x_label: str = "", y_label: str = "") -> dict:
    """Mixed bar + line chart."""
    chart_datasets = []
    bar_colors = _MULTI_COLORS
    line_colors = ["#f43f5e", "#10b981", "#f59e0b", "#3b82f6"]
    for i, ds in enumerate(bar_datasets):
        color = bar_colors[i % len(bar_colors)]
        chart_datasets.append({
            "type": "bar",
            "label": ds["label"],
            "data": ds["data"],
            "backgroundColor": color + "bb",
            "borderColor": color,
            "borderWidth": 1,
            "borderRadius": 4,
            "borderSkipped": False,
        })
    for i, ds in enumerate(line_datasets):
        color = line_colors[i % len(line_colors)]
        chart_datasets.append({
            "type": "line",
            "label": ds["label"],
            "data": ds["data"],
            "borderColor": color,
            "backgroundColor": color + "22",
            "tension": 0.4,
            "fill": False,
            "pointRadius": 4,
            "pointHoverRadius": 6,
            "pointBackgroundColor": color,
            "pointBorderColor": "#ffffff",
            "pointBorderWidth": 2,
            "borderWidth": 2.5,
            "yAxisID": "y1" if i > 0 else "y",
        })
    return {
        "type": "bar",
        "data": {"labels": labels, "datasets": chart_datasets},
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "animation": _ANIMATION_OPTS,
            "plugins": {
                "legend": {"display": True, "position": "top", "labels": {"color": "#475569", "font": {"size": 12}}},
                "tooltip": _TOOLTIP_OPTS,
            },
            "scales": _scale_opts(x_label, y_label),
        },
    }


def _funnel_config(labels: list, values: list, label: str, palette: str = "indigo") -> dict:
    """Funnel chart – simulated using descending horizontal bars."""
    pairs = sorted(zip(labels, values), key=lambda x: -x[1])
    sorted_labels = [p[0] for p in pairs]
    sorted_values = [p[1] for p in pairs]
    colors = _resolve_palette(palette, len(sorted_labels))
    return {
        "type": "bar",
        "data": {
            "labels": sorted_labels,
            "datasets": [{
                "label": label,
                "data": sorted_values,
                "backgroundColor": colors,
                "borderRadius": 4,
                "borderSkipped": False,
            }],
        },
        "options": {
            "indexAxis": "y",
            "responsive": True,
            "maintainAspectRatio": False,
            "animation": _ANIMATION_OPTS,
            "plugins": {
                "legend": {"display": False},
                "tooltip": _TOOLTIP_OPTS,
            },
            "scales": {
                "x": {"grid": {"display": False}, "ticks": {"color": "#94a3b8"}},
                "y": {"grid": {"display": False}, "ticks": {"color": "#475569", "font": {"weight": "600"}}},
            },
        },
        "_widget_hint": "funnel",
    }


def _gauge_config(value: float, min_val: float, max_val: float, label: str, palette: str = "indigo") -> dict:
    """Gauge chart – doughnut half showing a single value."""
    colors = _resolve_palette(palette, 1)
    color = colors[0]
    pct = max(0.0, min(1.0, (value - min_val) / (max_val - min_val) if max_val != min_val else 0))
    fill_val = round(pct * 100, 1)
    return {
        "type": "doughnut",
        "data": {
            "labels": [label, ""],
            "datasets": [{
                "data": [fill_val, 100 - fill_val],
                "backgroundColor": [color, "#e2e8f0"],
                "borderWidth": 0,
                "cutout": "78%",
                "circumference": 180,
                "rotation": -90,
            }],
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "animation": _ANIMATION_OPTS,
            "plugins": {
                "legend": {"display": False},
                "tooltip": {"enabled": False},
            },
        },
        "gauge_meta": {"value": value, "min": min_val, "max": max_val, "label": label},
    }


def _waterfall_config(labels: list, values: list, label: str, palette: str = "indigo",
                      x_label: str = "", y_label: str = "") -> dict:
    """Waterfall chart using stacked bars (transparent base + positive/negative bars)."""
    colors = _resolve_palette(palette, 2)
    pos_color = colors[0]
    neg_color = "#f43f5e"
    bar_colors = [pos_color if v >= 0 else neg_color for v in values]
    running = 0.0
    bases = []
    for v in values:
        if v >= 0:
            bases.append(round(running, 4))
        else:
            bases.append(round(running + v, 4))
        running += v
    abs_values = [abs(v) for v in values]
    return {
        "type": "bar",
        "data": {
            "labels": labels,
            "datasets": [
                {
                    "label": "_base",
                    "data": bases,
                    "backgroundColor": "rgba(0,0,0,0)",
                    "borderColor": "rgba(0,0,0,0)",
                    "stack": "s",
                    "borderSkipped": False,
                },
                {
                    "label": label,
                    "data": abs_values,
                    "backgroundColor": bar_colors,
                    "borderRadius": 4,
                    "borderSkipped": False,
                    "stack": "s",
                },
            ],
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "animation": _ANIMATION_OPTS,
            "plugins": {
                "legend": {"display": False},
                "tooltip": _TOOLTIP_OPTS,
            },
            "scales": {
                "x": {"grid": {"display": False}, "stacked": True, "ticks": {"color": "#94a3b8"}},
                "y": {"stacked": True, "grid": {"color": "rgba(0,0,0,0.05)"}, "ticks": {"color": "#94a3b8"}},
            },
        },
        "_widget_hint": "waterfall",
    }


def generate_widget_specs_from_version(dataset_version) -> list[dict]:
    """Read the saved dataset file and generate real chart widget specs."""
    from pathlib import Path

    file_path = dataset_version.source_file.path
    name = Path(file_path).name.lower()

    try:
        if name.endswith(".csv"):
            df = pd.read_csv(file_path)
        elif name.endswith((".xlsx", ".xlsm")):
            df = pd.read_excel(file_path)
        elif name.endswith(".json"):
            df = pd.read_json(file_path)
        else:
            return []
    except Exception:
        return []

    profile = build_profile_summary(df)
    specs: list[dict] = []
    position = 1

    # 1. KPI – total rows
    specs.append({
        "title": "Total Rows",
        "widget_type": "kpi",
        "config": {"kpi": "rows", "value": f"{profile.total_rows:,}"},
        "position": position,
    })
    position += 1

    # 2. KPI – total for first numeric column
    if profile.suggested_measures:
        measure = profile.suggested_measures[0]
        try:
            total = df[measure].sum()
            specs.append({
                "title": f"Total {measure}",
                "widget_type": "kpi",
                "config": {"kpi": measure, "value": f"{total:,.0f}"},
                "position": position,
            })
            position += 1
        except Exception:
            pass

    # 3. Bar chart – top dimension by first measure
    if profile.suggested_dimensions and profile.suggested_measures:
        dim = profile.suggested_dimensions[0]
        measure = profile.suggested_measures[0]
        try:
            top = df.groupby(dim)[measure].sum().nlargest(10)
            labels = [str(l) for l in top.index.tolist()]
            values = [round(float(v), 2) for v in top.values.tolist()]
            specs.append({
                "title": f"{measure} by {dim}",
                "widget_type": "bar",
                "config": _bar_config(labels, values, measure),
                "position": position,
            })
            position += 1
        except Exception:
            pass

    # 4. Pie chart – value counts of first categorical dimension
    if profile.suggested_dimensions:
        dim = profile.suggested_dimensions[0]
        try:
            vc = df[dim].value_counts().head(6)
            labels = [str(l) for l in vc.index.tolist()]
            values = [int(v) for v in vc.values.tolist()]
            specs.append({
                "title": f"Distribution: {dim}",
                "widget_type": "pie",
                "config": _pie_config(labels, values),
                "position": position,
            })
            position += 1
        except Exception:
            pass

    # 5. Line chart – time series if a date-like column exists
    date_cols = [
        c for c in df.columns
        if any(k in str(c).lower() for k in ["date", "month", "year", "period", "quarter"])
    ]
    if date_cols and profile.suggested_measures:
        date_col = date_cols[0]
        measure = profile.suggested_measures[0]
        try:
            tmp = df[[date_col, measure]].copy()
            tmp[date_col] = pd.to_datetime(tmp[date_col], errors="coerce")
            tmp = tmp.dropna(subset=[date_col])
            trend = tmp.groupby(tmp[date_col].dt.to_period("M"))[measure].sum()
            if len(trend) >= 2:
                labels = [str(p) for p in trend.index.tolist()]
                values = [round(float(v), 2) for v in trend.values.tolist()]
                specs.append({
                    "title": f"{measure} over time",
                    "widget_type": "line",
                    "config": _line_config(labels, values, measure),
                    "position": position,
                })
                position += 1
        except Exception:
            pass

    # 6. Second bar chart – second dimension vs first measure (if available)
    if len(profile.suggested_dimensions) > 1 and profile.suggested_measures:
        dim2 = profile.suggested_dimensions[1]
        measure = profile.suggested_measures[0]
        try:
            top2 = df.groupby(dim2)[measure].sum().nlargest(8)
            labels = [str(l) for l in top2.index.tolist()]
            values = [round(float(v), 2) for v in top2.values.tolist()]
            specs.append({
                "title": f"{measure} by {dim2}",
                "widget_type": "bar",
                "config": _bar_config(labels, values, measure),
                "position": position,
            })
            position += 1
        except Exception:
            pass

    return specs
