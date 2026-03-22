from __future__ import annotations

from dataclasses import dataclass
import logging

import pandas as pd

logger = logging.getLogger(__name__)


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


def _to_datetime_safe(values: pd.Series) -> pd.Series:
    """Parse datetimes while avoiding noisy inference warnings across pandas versions."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Could not infer format, so each element will be parsed individually",
            category=UserWarning,
        )
        try:
            return pd.to_datetime(values, format="mixed", errors="coerce")
        except TypeError:
            # pandas<2.0 does not support format="mixed"
            return pd.to_datetime(values, errors="coerce")


def detect_and_clean_headers(df: pd.DataFrame) -> pd.DataFrame:
    """Dynamically detect and clean column headers.

    - Detects when first data row is actually headers (>50% Unnamed: columns)
    - Strips whitespace from column names
    - Fills empty/NaN headers with Column_N
    - Deduplicates column names by appending _2, _3 suffixes
    """
    cols = list(df.columns)

    # If more than half the columns are auto-named "Unnamed: N", try promoting first row as header
    unnamed_count = sum(1 for c in cols if str(c).startswith("Unnamed:") or str(c).strip() == "")
    if unnamed_count > len(cols) * 0.5 and len(df) > 0:
        first_row_vals = [str(v).strip() for v in df.iloc[0]]
        # Only promote if first row looks like strings (not all numeric)
        non_numeric = sum(1 for v in first_row_vals if not v.replace(".", "").replace("-", "").isdigit())
        if non_numeric >= len(first_row_vals) * 0.5:
            new_cols = [
                v if v and v.lower() not in ("nan", "none", "") else f"Column_{i+1}"
                for i, v in enumerate(first_row_vals)
            ]
            df = df.iloc[1:].reset_index(drop=True)
            df.columns = new_cols
            cols = new_cols

    # Strip whitespace and replace empty/NaN names
    cleaned: list[str] = []
    for i, c in enumerate(cols):
        name = str(c).strip()
        if not name or name.lower() in ("nan", "none", ""):
            name = f"Column_{i + 1}"
        cleaned.append(name)

    # Deduplicate
    seen: dict[str, int] = {}
    deduped: list[str] = []
    for name in cleaned:
        if name in seen:
            seen[name] += 1
            deduped.append(f"{name}_{seen[name]}")
        else:
            seen[name] = 0
            deduped.append(name)

    df.columns = deduped
    return df


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

    # Dynamic header detection and column name cleaning
    df = detect_and_clean_headers(df)

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


def _compute_kpi_trend(df: pd.DataFrame, measure: str) -> dict:
    """Compute trend metadata for a KPI metric.

    Returns dict with: trend_dir, trend_pct, secondary_label, secondary_value, sparkline.
    All fields are safe defaults when computation is not possible.
    """
    if measure not in df.columns or not pd.api.types.is_numeric_dtype(df[measure]):
        return {}

    col = df[measure].dropna()
    if len(col) < 2:
        return {}

    mean_val = float(col.mean())
    sparkline: list[float] = []
    trend_dir = "flat"
    trend_pct = 0.0
    secondary_label = "avg"
    secondary_value = f"{mean_val:,.1f}"

    # Try period-over-period comparison using a date column
    date_cols = [
        c for c in df.columns
        if any(k in str(c).lower() for k in ["date", "month", "year", "period", "quarter"])
    ]
    if date_cols:
        try:
            tmp = df[[date_cols[0], measure]].copy()
            tmp[date_cols[0]] = _to_datetime_safe(tmp[date_cols[0]])
            tmp = tmp.dropna(subset=[date_cols[0]]).sort_values(date_cols[0])
            monthly = tmp.groupby(tmp[date_cols[0]].dt.to_period("M"))[measure].sum()
            if len(monthly) >= 2:
                sparkline = [round(float(v), 2) for v in monthly.values[-12:]]
                last = float(monthly.values[-1])
                prev = float(monthly.values[-2])
                if prev != 0:
                    trend_pct = round((last - prev) / abs(prev) * 100, 1)
                    trend_dir = "up" if trend_pct > 0 else ("down" if trend_pct < 0 else "flat")
                secondary_label = f"vs {monthly.index[-2]}"
                secondary_value = f"{prev:,.0f}"
        except Exception:
            pass

    # Fallback sparkline: split data into chunks
    if not sparkline and len(col) >= 4:
        chunk_size = max(1, len(col) // 12)
        chunks = [
            float(col.iloc[i: i + chunk_size].sum())
            for i in range(0, len(col), chunk_size)
        ]
        sparkline = [round(v, 2) for v in chunks[-12:]]
        if len(sparkline) >= 2 and sparkline[-2] != 0:
            trend_pct = round((sparkline[-1] - sparkline[-2]) / abs(sparkline[-2]) * 100, 1)
            trend_dir = "up" if trend_pct > 0 else ("down" if trend_pct < 0 else "flat")

    # Compute sparkline_pct: normalize sparkline to 8-100% range for bar heights
    sparkline_pct: list[int] = []
    if sparkline:
        sp_max = max(abs(v) for v in sparkline) or 1
        sparkline_pct = [max(8, round(abs(v) / sp_max * 100)) for v in sparkline]

    return {
        "trend_dir": trend_dir,
        "trend_pct": abs(trend_pct),
        "secondary_label": secondary_label,
        "secondary_value": secondary_value,
        "sparkline": sparkline,
        "sparkline_pct": sparkline_pct,
        "avg": round(float(col.mean()), 2),
        "max_val": round(float(col.max()), 2),
        "min_val": round(float(col.min()), 2),
        "count": len(col),
    }


def build_widget_suggestions(profile: ProfileSummary) -> list[WidgetSuggestion]:
    suggestions: list[WidgetSuggestion] = []

    if profile.suggested_measures and profile.suggested_dimensions:
        dim = profile.suggested_dimensions[0]
        measure = profile.suggested_measures[0]
        suggestions.append(WidgetSuggestion(
            title=f"{_humanize_col(measure)} by {_humanize_col(dim)}",
            chart_type="bar",
            description=f"Compare {_humanize_col(measure)} across {_humanize_col(dim)} categories",
        ))

    if profile.suggested_measures and profile.suggested_dimensions:
        dim = profile.suggested_dimensions[0]
        suggestions.append(WidgetSuggestion(
            title=f"{_humanize_col(dim)} Share",
            chart_type="pie",
            description=f"Proportion breakdown of {_humanize_col(dim)} values",
        ))

    date_like_dims = [d for d in profile.suggested_dimensions if any(k in d.lower() for k in ["date", "month", "year", "period", "quarter"])]
    if date_like_dims and profile.suggested_measures:
        measure = profile.suggested_measures[0]
        suggestions.append(WidgetSuggestion(
            title=f"{_humanize_col(measure)} Trend Over Time",
            chart_type="line",
            description=f"Track {_humanize_col(measure)} trend by {_humanize_col(date_like_dims[0])}",
        ))

    if profile.suggested_measures:
        measure = profile.suggested_measures[0]
        suggestions.append(WidgetSuggestion(
            title=f"Total {_humanize_col(measure)}",
            chart_type="kpi",
            description=f"Sum of all {_humanize_col(measure)} values — key headline metric",
        ))

    if profile.duplicate_rows > 0:
        suggestions.append(WidgetSuggestion(
            title="Duplicate Records",
            chart_type="kpi",
            description=f"{profile.duplicate_rows} duplicate rows detected in this dataset",
        ))

    if not suggestions:
        suggestions.append(WidgetSuggestion(
            title="Key Metrics Overview",
            chart_type="kpi",
            description="Summary of key metrics from your dataset",
        ))

    return suggestions[:6]


# ── Dataset cleaning ────────────────────────────────────────────────────────────

@dataclass
class CleanResult:
    dataframe: pd.DataFrame
    rows_before: int
    rows_after: int
    duplicates_removed: int
    missing_filled: int
    missing_rows_dropped: int


def clean_dataframe(
    df: pd.DataFrame,
    drop_duplicates: bool = False,
    missing_strategy: str = "keep",
) -> CleanResult:
    """Clean a DataFrame based on user-selected options.

    missing_strategy choices:
      - "keep"       – do nothing with missing values
      - "drop_rows"  – drop rows that have any missing value
      - "fill_zero"  – fill numeric NaN with 0, text NaN with empty string
      - "fill_mean"  – fill numeric NaN with column mean, text NaN with empty string
    """
    rows_before = len(df)
    duplicates_removed = 0
    missing_filled = 0
    missing_rows_dropped = 0

    if drop_duplicates:
        before = len(df)
        df = df.drop_duplicates()
        duplicates_removed = before - len(df)

    if missing_strategy == "drop_rows":
        before = len(df)
        df = df.dropna()
        missing_rows_dropped = before - len(df)
    elif missing_strategy in ("fill_zero", "fill_mean"):
        for col in df.columns:
            null_count = int(df[col].isna().sum())
            if null_count == 0:
                continue
            if pd.api.types.is_numeric_dtype(df[col]):
                fill_val = df[col].mean() if missing_strategy == "fill_mean" else 0
                df[col] = df[col].fillna(fill_val)
            else:
                df[col] = df[col].fillna("")
            missing_filled += null_count

    return CleanResult(
        dataframe=df,
        rows_before=rows_before,
        rows_after=len(df),
        duplicates_removed=duplicates_removed,
        missing_filled=missing_filled,
        missing_rows_dropped=missing_rows_dropped,
    )


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

    # Dynamic header detection and column name cleaning
    df = detect_and_clean_headers(df)

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
    "backgroundColor": "rgba(15,23,42,0.94)",
    "titleColor": "#f8fafc",
    "bodyColor": "#cbd5e1",
    "borderColor": "rgba(99,102,241,0.35)",
    "borderWidth": 1,
    "padding": 12,
    "cornerRadius": 10,
    "displayColors": True,
    "boxWidth": 10,
    "boxHeight": 10,
    "caretSize": 6,
    "titleFont": {"size": 12, "weight": "600"},
    "bodyFont": {"size": 12},
}

_ANIMATION_OPTS = {
    "duration": 700,
    "easing": "easeInOutCubic",
}

_LEGEND_OPTS = {
    "display": True,
    "position": "top",
    "align": "start",
    "labels": {
        "color": "#475569",
        "font": {"size": 11, "weight": "500"},
        "padding": 16,
        "usePointStyle": True,
        "pointStyleWidth": 8,
    },
}


def _scale_opts(x_label: str = "", y_label: str = "") -> dict:
    x = {
        "grid": {"display": False},
        "border": {"display": False},
        "ticks": {"color": "#94a3b8", "font": {"size": 11}, "maxRotation": 35},
    }
    y = {
        "grid": {"color": "rgba(148,163,184,0.12)", "drawBorder": False},
        "border": {"display": False, "dash": [4, 4]},
        "ticks": {"color": "#94a3b8", "font": {"size": 11}},
    }
    if x_label:
        x["title"] = {"display": True, "text": x_label, "color": "#64748b", "font": {"size": 11, "weight": "600"}, "padding": {"top": 6}}
    if y_label:
        y["title"] = {"display": True, "text": y_label, "color": "#64748b", "font": {"size": 11, "weight": "600"}, "padding": {"bottom": 6}}
    return {"x": x, "y": y}


def _resolve_palette(palette_name: str, n: int) -> list:
    colors = PALETTES.get(palette_name, DEFAULT_PALETTE)
    # cycle if needed
    return [colors[i % len(colors)] for i in range(n)]


def _humanize_col(name: str) -> str:
    """Convert a raw column name to a human-readable title.

    Examples:
        total_revenue     → Total Revenue
        salesAmount       → Sales Amount
        num_orders_ytd    → Num Orders Ytd
        customerLifetimeValue → Customer Lifetime Value
    """
    import re as _re
    s = _re.sub(r'([a-z])([A-Z])', r'\1 \2', str(name))
    s = s.replace('_', ' ').replace('-', ' ')
    s = _re.sub(r'\s+', ' ', s).strip()
    return s.title()


def _detect_kpi_meta(col_name: str) -> dict:
    """Detect KPI display metadata (format + icon type) from a column name.

    Returns a dict with:
        format: 'currency' | 'percent' | 'count' | 'number'
        icon:   'money' | 'percent' | 'people' | 'clock' | 'chart'
    """
    lower = str(col_name).lower()
    if any(k in lower for k in [
        'revenue', 'sales', 'profit', 'cost', 'price', 'amount', 'income',
        'spend', 'budget', 'earning', 'margin', 'value', 'gmv', 'arpu', 'ltv',
        'fee', 'payment', 'invoice', 'receipt', 'cash', 'dollar', 'usd', 'eur',
    ]):
        return {'format': 'currency', 'icon': 'money'}
    if any(k in lower for k in [
        'rate', 'ratio', 'pct', 'percent', 'share', 'growth', 'churn',
        'conversion', 'efficiency', 'utilization', 'retention', 'accuracy',
    ]):
        return {'format': 'percent', 'icon': 'percent'}
    if any(k in lower for k in [
        'count', 'num', 'number', 'qty', 'quantity', 'volume', 'orders',
        'transactions', 'users', 'customers', 'visitors', 'sessions',
        'clicks', 'leads', 'signups', 'views', 'records', 'rows',
    ]):
        return {'format': 'count', 'icon': 'people'}
    if any(k in lower for k in [
        'days', 'hours', 'minutes', 'duration', 'time', 'age', 'tenure',
        'latency', 'ttl', 'ttfb', 'response',
    ]):
        return {'format': 'number', 'icon': 'clock'}
    if col_name in ('rows', 'records', 'total_rows'):
        return {'format': 'count', 'icon': 'people'}
    return {'format': 'number', 'icon': 'chart'}


def apply_df_filters(df: pd.DataFrame, filters: list) -> pd.DataFrame:
    """Apply a list of filter dicts to a DataFrame.

    Each filter dict has: column, filter_type, value.
    Supported filter_type values: "dropdown", "radio", "multiselect", "range".
    """
    if not filters:
        return df
    result = df.copy()
    for f in filters:
        col = f.get("column", "")
        ftype = f.get("filter_type", "dropdown")
        value = f.get("value")
        if not col or col not in result.columns or value is None or value == "" or value == []:
            continue
        try:
            if ftype in ("dropdown", "radio"):
                if str(value) != "__all__":
                    result = result[result[col].astype(str) == str(value)]
            elif ftype == "multiselect":
                if isinstance(value, list) and value and value != ["__all__"]:
                    str_values = [str(v) for v in value]
                    result = result[result[col].astype(str).isin(str_values)]
            elif ftype == "range":
                if isinstance(value, list) and len(value) == 2:
                    lo, hi = value
                    if pd.api.types.is_numeric_dtype(result[col]):
                        result = result[(result[col] >= float(lo)) & (result[col] <= float(hi))]
        except Exception:
            pass
    return result


def _bar_config(labels: list, values: list, label: str, palette: str = "indigo",
                x_label: str = "", y_label: str = "") -> dict:
    colors = _resolve_palette(palette, len(labels))
    human_label = _humanize_col(label)
    return {
        "type": "bar",
        "data": {
            "labels": labels,
            "datasets": [{
                "label": human_label,
                "data": values,
                "backgroundColor": colors,
                "borderRadius": 8,
                "borderSkipped": False,
                "hoverBackgroundColor": [c + "dd" for c in colors],
                "hoverBorderColor": [c for c in colors],
                "hoverBorderWidth": 2,
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
            "label": _humanize_col(ds["label"]),
            "data": ds["data"],
            "backgroundColor": color + "dd",
            "borderColor": color,
            "borderWidth": 1,
            "borderRadius": 6,
            "borderSkipped": False,
            "hoverBackgroundColor": color,
        })
    return {
        "type": "bar",
        "data": {"labels": labels, "datasets": chart_datasets},
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "animation": _ANIMATION_OPTS,
            "plugins": {
                "legend": _LEGEND_OPTS,
                "tooltip": _TOOLTIP_OPTS,
            },
            "scales": _scale_opts(x_label, y_label),
        },
    }


def _line_config(labels: list, values: list, label: str, palette: str = "indigo",
                 x_label: str = "", y_label: str = "") -> dict:
    colors = _resolve_palette(palette, 1)
    border = colors[0]
    human_label = _humanize_col(label)
    return {
        "type": "line",
        "data": {
            "labels": labels,
            "datasets": [{
                "label": human_label,
                "data": values,
                "borderColor": border,
                "backgroundColor": border + "18",
                "tension": 0.45,
                "fill": False,
                "pointRadius": 4,
                "pointHoverRadius": 7,
                "pointBackgroundColor": border,
                "pointBorderColor": "#ffffff",
                "pointBorderWidth": 2.5,
                "borderWidth": 2.5,
                "spanGaps": True,
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
            "interaction": {"mode": "index", "intersect": False},
        },
    }


def _multi_line_config(labels: list, datasets: list[dict], palette: str = "indigo",
                       x_label: str = "", y_label: str = "") -> dict:
    chart_datasets = []
    for i, ds in enumerate(datasets):
        color = _MULTI_COLORS[i % len(_MULTI_COLORS)]
        chart_datasets.append({
            "label": _humanize_col(ds["label"]),
            "data": ds["data"],
            "borderColor": color,
            "backgroundColor": color + "18",
            "tension": 0.45,
            "fill": False,
            "pointRadius": 4,
            "pointHoverRadius": 7,
            "pointBackgroundColor": color,
            "pointBorderColor": "#ffffff",
            "pointBorderWidth": 2.5,
            "borderWidth": 2.5,
            "spanGaps": True,
        })
    return {
        "type": "line",
        "data": {"labels": labels, "datasets": chart_datasets},
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "animation": _ANIMATION_OPTS,
            "plugins": {
                "legend": _LEGEND_OPTS,
                "tooltip": _TOOLTIP_OPTS,
            },
            "scales": _scale_opts(x_label, y_label),
            "interaction": {"mode": "index", "intersect": False},
        },
    }


def _area_config(labels: list, values: list, label: str, palette: str = "indigo",
                 x_label: str = "", y_label: str = "") -> dict:
    colors = _resolve_palette(palette, 1)
    border = colors[0]
    human_label = _humanize_col(label)
    return {
        "type": "line",
        "data": {
            "labels": labels,
            "datasets": [{
                "label": human_label,
                "data": values,
                "borderColor": border,
                "backgroundColor": {
                    "type": "linear",
                    "x": 0, "y": 0, "x2": 0, "y2": 1,
                    "colorStops": [
                        {"offset": 0, "color": border + "55"},
                        {"offset": 1, "color": border + "05"},
                    ],
                },
                "tension": 0.45,
                "fill": True,
                "pointRadius": 4,
                "pointHoverRadius": 7,
                "pointBackgroundColor": border,
                "pointBorderColor": "#ffffff",
                "pointBorderWidth": 2.5,
                "borderWidth": 2.5,
                "spanGaps": True,
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
            "interaction": {"mode": "index", "intersect": False},
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
                "hoverOffset": 12,
                "borderWidth": 3,
                "borderColor": "#ffffff",
                "hoverBorderColor": "#ffffff",
                "hoverBorderWidth": 3,
            }],
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "animation": _ANIMATION_OPTS,
            "plugins": {
                "legend": {
                    "position": "bottom",
                    "labels": {
                        "font": {"size": 11, "weight": "500"},
                        "color": "#64748b",
                        "padding": 16,
                        "usePointStyle": True,
                        "pointStyleWidth": 8,
                    },
                },
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
                "hoverOffset": 12,
                "borderWidth": 3,
                "borderColor": "#ffffff",
                "hoverBorderColor": "#ffffff",
                "hoverBorderWidth": 3,
            }],
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "cutout": "70%",
            "animation": _ANIMATION_OPTS,
            "plugins": {
                "legend": {
                    "position": "bottom",
                    "labels": {
                        "font": {"size": 11, "weight": "500"},
                        "color": "#64748b",
                        "padding": 16,
                        "usePointStyle": True,
                        "pointStyleWidth": 8,
                    },
                },
                "tooltip": _TOOLTIP_OPTS,
            },
        },
    }


def _hbar_config(labels: list, values: list, label: str, palette: str = "indigo",
                 x_label: str = "", y_label: str = "") -> dict:
    colors = _resolve_palette(palette, len(labels))
    human_label = _humanize_col(label)
    return {
        "type": "bar",
        "data": {
            "labels": labels,
            "datasets": [{
                "label": human_label,
                "data": values,
                "backgroundColor": colors,
                "borderRadius": 5,
                "borderSkipped": False,
                "hoverBackgroundColor": [c + "dd" for c in colors],
                "hoverBorderColor": colors,
                "hoverBorderWidth": 2,
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


def _get_ai_client():
    """Return (client, model) tuple for DeepSeek if configured, else (None, None)."""
    import importlib.util
    from django.conf import settings

    api_key = getattr(settings, "DEEPSEEK_API_KEY", "")
    if not api_key:
        return None, None
    if importlib.util.find_spec("openai") is None:
        return None, None
    openai_module = __import__("openai")
    model = getattr(settings, "DEEPSEEK_MODEL", "deepseek-chat")
    max_retries = int(getattr(settings, "DEEPSEEK_MAX_RETRIES", 0))
    client = openai_module.OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com",
        max_retries=max_retries,
    )
    return client, model


def ai_detect_column_roles(df: pd.DataFrame, profile: "ProfileSummary") -> dict:
    """AI-powered column role detection: classifies each column as measure/dimension/date/id.

    Returns dict: {column_name: {role, agg, label, cardinality}}

    Roles:
    - 'measure': quantitative, aggregatable (revenue, amount, count, score, rate, price)
    - 'dimension': categorical grouping variables (region, category, name, type, status)
    - 'date': temporal columns (date, month, year, quarter, period, timestamp)
    - 'id': identifier/key columns to skip (id, uuid, code, ref, key, index)

    agg: best aggregation ('sum', 'avg', 'count', 'max', 'min', 'group', 'none')
    cardinality: for dimensions ('low'<10, 'medium'=10-50, 'high'>50), None for others
    """
    import json as _json
    import re as _re

    client, model = _get_ai_client()

    # Build sample values per column
    col_samples: dict = {}
    for col in list(df.columns[:50]):
        try:
            vals = df[col].dropna().head(5).tolist()
            col_samples[str(col)] = [str(v)[:50] for v in vals]
        except Exception:
            pass

    # Numeric stats
    col_stats: dict = {}
    for col in profile.numeric_columns[:20]:
        try:
            col_stats[str(col)] = {
                "min": round(float(df[col].min()), 4),
                "max": round(float(df[col].max()), 4),
                "mean": round(float(df[col].mean()), 4),
                "null_pct": round(float(df[col].isna().mean() * 100), 1),
            }
        except Exception:
            pass

    def _heuristic_roles() -> dict:
        roles: dict = {}
        for col in profile.numeric_columns:
            roles[str(col)] = {
                "role": "measure", "agg": "sum",
                "label": _humanize_col(col), "cardinality": None,
            }
        for col in profile.categorical_columns:
            try:
                card = int(df[col].nunique(dropna=True))
                tier = "low" if card < 10 else "medium" if card < 50 else "high"
            except Exception:
                tier = "medium"
            roles[str(col)] = {
                "role": "dimension", "agg": "group",
                "label": _humanize_col(col), "cardinality": tier,
            }
        date_keywords = ["date", "month", "year", "period", "quarter", "time", "timestamp"]
        for col in df.columns:
            col_lower = str(col).lower()
            if any(k in col_lower for k in date_keywords):
                roles[str(col)] = {
                    "role": "date", "agg": "none",
                    "label": _humanize_col(col), "cardinality": None,
                }
        return roles

    if client is None:
        return _heuristic_roles()

    payload = {
        "columns": [str(c) for c in df.columns[:50]],
        "sample_values": col_samples,
        "numeric_cols": [str(c) for c in profile.numeric_columns[:20]],
        "categorical_cols": [str(c) for c in profile.categorical_columns[:20]],
        "column_stats": col_stats,
        "total_rows": int(profile.total_rows),
    }

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a senior BI data architect specializing in dimensional modeling and column classification.\n\n"
                        "Analyze every column provided and classify it into one of four roles:\n"
                        "1. 'measure': Quantitative, aggregatable values — revenue, sales, count, amount, "
                        "quantity, score, rate, price, cost, profit, margin, duration, weight, distance\n"
                        "2. 'dimension': Categorical grouping variables — region, category, name, type, "
                        "status, segment, product, department, country, channel, brand\n"
                        "3. 'date': Temporal columns — date, month, year, quarter, period, timestamp, "
                        "created_at, updated_at, order_date, event_date\n"
                        "4. 'id': Identifier/key columns to skip in aggregations — id, uuid, code, "
                        "ref, key, index, serial, row_number, record_id\n\n"
                        "For each column also specify:\n"
                        "- agg: best aggregation method ('sum' for totals, 'avg' for averages/rates/ratios, "
                        "'count' for occurrences, 'nunique' for distinct counts (e.g. unique suppliers/customers/products), "
                        "'max'/'min' for extremes, 'group' for dimensions, 'none' for ids/dates)\n"
                        "- label: human-friendly business label (e.g. 'total_revenue_usd' → 'Total Revenue (USD)')\n"
                        "- cardinality: for dimensions only ('low'=<10 unique, 'medium'=10-50, 'high'>50); "
                        "use null for measures, dates, ids\n\n"
                        "RETURN ONLY valid JSON (no markdown, no extra text):\n"
                        "{\"roles\": {\"column_name\": {\"role\": \"measure|dimension|date|id\", "
                        "\"agg\": \"sum|avg|count|nunique|max|min|group|none\", "
                        "\"label\": \"Business Label\", \"cardinality\": \"low|medium|high|null\"}}}"
                    ),
                },
                {"role": "user", "content": _json.dumps(payload)},
            ],
            temperature=0.05,
            stream=False,
            timeout=20,
        )
        content = ((response.choices[0].message.content) or "").strip()
        match = _re.search(r"\{.*\}", content, flags=_re.DOTALL)
        parsed = _json.loads(match.group(0) if match else content)
        roles = parsed.get("roles", {})
        if isinstance(roles, dict) and len(roles) > 0:
            # Merge: ensure all df columns have a role (fallback for any missed)
            fallback = _heuristic_roles()
            for col in df.columns:
                if str(col) not in roles:
                    roles[str(col)] = fallback.get(str(col), {"role": "dimension", "agg": "group", "label": _humanize_col(col), "cardinality": "medium"})
            return roles
    except Exception:
        pass

    return _heuristic_roles()


def ai_generate_comprehensive_insights(
    df: pd.DataFrame,
    profile: "ProfileSummary",
    dashboard_title: str = "",
    widget_titles: list | None = None,
) -> dict:
    """Generate a comprehensive AI-powered dashboard narrative by a senior data analyst.

    Returns dict with keys:
    - executive_summary: str  (2-3 sentence synthesis of the data story)
    - key_findings: list[str]  (4-5 specific, numbered findings with data)
    - strategic_recs: list[str]  (2-3 action-oriented recommendations)
    - data_health: str  (1 sentence on data quality implications)
    - analyst_note: str  (expert commentary on business implications)
    """
    import json as _json
    import re as _re

    client, model = _get_ai_client()

    # Build rich statistics context
    numeric_stats: dict = {}
    for col in profile.numeric_columns[:12]:
        try:
            s = df[col].dropna()
            numeric_stats[str(col)] = {
                "sum": round(float(s.sum()), 2),
                "mean": round(float(s.mean()), 2),
                "median": round(float(s.median()), 2),
                "std": round(float(s.std()), 2),
                "min": round(float(s.min()), 2),
                "max": round(float(s.max()), 2),
                "count_non_null": int(s.count()),
                "human_label": _humanize_col(col),
            }
        except Exception:
            pass

    cat_top_values: dict = {}
    for col in profile.categorical_columns[:6]:
        try:
            vc = df[col].value_counts().head(5)
            cat_top_values[str(col)] = {
                "top_values": {str(k): int(v) for k, v in vc.items()},
                "unique_count": int(df[col].nunique(dropna=True)),
                "human_label": _humanize_col(col),
            }
        except Exception:
            pass

    date_range_info: dict = {}
    for col in df.columns:
        if any(k in str(col).lower() for k in ["date", "month", "year", "period", "quarter"]):
            try:
                tmp = _to_datetime_safe(df[col]).dropna()
                if len(tmp) > 0:
                    date_range_info[str(col)] = {
                        "min": str(tmp.min().date()),
                        "max": str(tmp.max().date()),
                        "span_days": int((tmp.max() - tmp.min()).days),
                        "human_label": _humanize_col(col),
                    }
            except Exception:
                pass

    def _heuristic_insights() -> dict:
        top_metric = profile.numeric_columns[0] if profile.numeric_columns else ""
        label = _humanize_col(top_metric) if top_metric else "records"
        try:
            total_val = f"{df[top_metric].sum():,.0f}" if top_metric else str(profile.total_rows)
        except Exception:
            total_val = str(profile.total_rows)
        findings = [
            f"Dataset contains {profile.total_rows:,} records across {profile.total_columns} dimensions, "
            f"providing a comprehensive view of {dashboard_title or 'business performance'}.",
        ]
        if top_metric:
            findings.append(
                f"Primary metric '{label}' totals {total_val}, "
                f"tracked across {len(profile.categorical_columns)} categorical dimensions."
            )
        if profile.categorical_columns:
            findings.append(
                f"Key segmentation dimensions include {', '.join(_humanize_col(c) for c in profile.categorical_columns[:3])}, "
                f"enabling multi-dimensional performance analysis."
            )
        if date_range_info:
            first_date_col = list(date_range_info.values())[0]
            findings.append(
                f"Data spans from {first_date_col['min']} to {first_date_col['max']} "
                f"({first_date_col['span_days']} days), enabling trend analysis."
            )
        return {
            "executive_summary": (
                f"This {dashboard_title or 'business'} dashboard analyzes {profile.total_rows:,} records "
                f"across {profile.total_columns} data dimensions. "
                f"Key metrics and distributions are visualized to support strategic decision-making and performance monitoring."
            ),
            "key_findings": findings[:5],
            "strategic_recs": [
                f"Prioritize deep-dive analysis into {_humanize_col(profile.suggested_measures[0]) if profile.suggested_measures else 'primary metrics'} to identify growth levers.",
                f"Segment performance across {_humanize_col(profile.suggested_dimensions[0]) if profile.suggested_dimensions else 'key dimensions'} to uncover variance and opportunity.",
                "Set up automated refresh schedules to monitor KPI trends and flag anomalies in near real-time.",
            ],
            "data_health": (
                f"Data completeness: {profile.missing_cells} missing cells detected across {profile.total_rows:,} rows "
                f"({round(profile.missing_cells / max(profile.total_rows * profile.total_columns, 1) * 100, 1)}% missing). "
                f"{'Consider imputation or exclusion before drawing conclusions.' if profile.missing_cells > 0 else 'Excellent data quality.'}"
            ),
            "analyst_note": (
                f"With {len(profile.numeric_columns)} measurable KPIs and {len(profile.categorical_columns)} "
                f"segmentation dimensions, this dataset is well-structured for executive-level BI reporting."
            ),
        }

    if client is None:
        return _heuristic_insights()

    payload = {
        "dashboard_title": str(dashboard_title or "Business Dashboard").strip(),
        "total_rows": int(profile.total_rows),
        "total_columns": int(profile.total_columns),
        "numeric_stats": numeric_stats,
        "categorical_summaries": cat_top_values,
        "date_ranges": date_range_info,
        "widget_titles": [str(t) for t in (widget_titles or [])[:20]],
        "data_quality": {
            "duplicate_rows": int(profile.duplicate_rows),
            "missing_cells": int(profile.missing_cells),
            "missing_pct": round(profile.missing_cells / max(profile.total_rows * profile.total_columns, 1) * 100, 1),
        },
    }

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a world-class Senior Data Analyst and Strategic Advisor with 20+ years of "
                        "international BI experience across Fortune 500 companies, investment banks, and "
                        "management consulting firms in North America, Europe, and Asia-Pacific.\n\n"
                        "TASK: Write a comprehensive, data-driven analytical narrative for this business dashboard.\n\n"
                        "REQUIREMENTS:\n"
                        "executive_summary: 2-3 sentences synthesizing the overall data story. "
                        "MUST mention the primary metric value, key trend, and business implication. "
                        "Write as an experienced analyst presenting to a C-suite audience.\n\n"
                        "key_findings: Exactly 4-5 specific, numbered insights. "
                        "EVERY finding MUST cite a specific number from the provided statistics. "
                        "Order by business impact (most impactful first). "
                        "Format: 'Finding [N]: [Specific insight with number and business context].'\n\n"
                        "strategic_recs: 2-3 action-oriented recommendations. "
                        "Each MUST specify WHO should do WHAT, by WHEN, and WHY with expected impact. "
                        "Be concrete, not generic.\n\n"
                        "data_health: 1 sentence on data completeness and its analytical implications.\n\n"
                        "analyst_note: 1 sentence expert commentary on what this data reveals about the "
                        "business's strategic position or operational maturity.\n\n"
                        "STYLE: Confident, authoritative, data-dense executive tone. "
                        "Never vague or generic. Always cite specific numbers. "
                        "Write as if preparing a board-level briefing document.\n\n"
                        "Return ONLY valid JSON (no markdown, no extra text):\n"
                        "{\"executive_summary\":\"...\","
                        "\"key_findings\":[\"Finding 1: ...\",\"Finding 2: ...\"],"
                        "\"strategic_recs\":[\"...\"],"
                        "\"data_health\":\"...\","
                        "\"analyst_note\":\"...\"}"
                    ),
                },
                {"role": "user", "content": _json.dumps(payload)},
            ],
            temperature=0.2,
            stream=False,
            timeout=30,
        )
        content = ((response.choices[0].message.content) or "").strip()
        match = _re.search(r"\{.*\}", content, flags=_re.DOTALL)
        parsed = _json.loads(match.group(0) if match else content)
        result = {
            "executive_summary": str(parsed.get("executive_summary", "")).strip(),
            "key_findings": [str(f).strip() for f in (parsed.get("key_findings") or []) if str(f).strip()][:5],
            "strategic_recs": [str(r).strip() for r in (parsed.get("strategic_recs") or []) if str(r).strip()][:3],
            "data_health": str(parsed.get("data_health", "")).strip(),
            "analyst_note": str(parsed.get("analyst_note", "")).strip(),
        }
        if result["executive_summary"]:
            return result
    except Exception:
        pass

    return _heuristic_insights()


def ai_generate_executive_summary(
    df: pd.DataFrame,
    profile: "ProfileSummary",
    dashboard_title: str = "",
    widget_titles: list | None = None,
) -> dict:
    """Generate a structured executive summary for the dashboard and PDF export.

    Returns a dict with keys:
      headline     – 1 punchy sentence capturing the main story
      findings     – list of 3-5 bullet strings (quantified insights)
      opportunities – list of 2-3 recommendation strings
      data_quality  – short string about data health
      generated_at  – ISO timestamp string
    """
    import json as _json
    import re as _re
    from datetime import datetime

    generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    fallback = {
        "headline": f"{dashboard_title or 'Dashboard'} — Data Summary",
        "findings": [],
        "opportunities": [],
        "data_quality": (
            f"{profile.total_rows:,} rows, {profile.total_columns} columns. "
            f"{profile.duplicate_rows} duplicate rows, {profile.missing_cells} missing cells."
        ),
        "generated_at": generated_at,
    }

    client, model = _get_ai_client()
    if client is None:
        # Heuristic fallback findings
        findings = []
        for m in profile.suggested_measures[:3]:
            try:
                total = df[m].sum()
                avg = df[m].mean()
                findings.append(f"{m}: total {total:,.0f} · avg {avg:,.1f} per record")
            except Exception:
                pass
        for d in profile.suggested_dimensions[:2]:
            try:
                top = df[d].value_counts().index[0]
                count = int(df[d].value_counts().values[0])
                findings.append(f"Most common {d}: '{top}' ({count:,} records)")
            except Exception:
                pass
        fallback["findings"] = findings
        return fallback

    # Rich statistical context
    stats: dict = {}
    for m in profile.suggested_measures[:8]:
        try:
            col = df[m].dropna()
            stats[m] = {
                "sum": round(float(col.sum()), 2),
                "mean": round(float(col.mean()), 2),
                "median": round(float(col.median()), 2),
                "min": round(float(col.min()), 2),
                "max": round(float(col.max()), 2),
                "std": round(float(col.std()), 2),
            }
        except Exception:
            pass

    top_values: dict = {}
    for d in profile.suggested_dimensions[:5]:
        try:
            vc = df[d].value_counts(dropna=True)
            top_values[d] = [
                {"value": str(idx), "count": int(cnt)}
                for idx, cnt in zip(vc.index[:5], vc.values[:5])
            ]
        except Exception:
            pass

    date_cols = [c for c in df.columns if any(k in str(c).lower() for k in ["date", "month", "year", "period", "quarter"])]
    date_range: dict = {}
    if date_cols:
        try:
            tmp = _to_datetime_safe(df[date_cols[0]]).dropna()
            if len(tmp) > 0:
                date_range = {
                    "column": date_cols[0],
                    "from": str(tmp.min().date()),
                    "to": str(tmp.max().date()),
                    "periods": int(tmp.dt.to_period("M").nunique()),
                }
        except Exception:
            pass

    payload = {
        "dashboard_title": dashboard_title,
        "widget_titles": (widget_titles or [])[:15],
        "total_rows": profile.total_rows,
        "total_columns": profile.total_columns,
        "duplicate_rows": profile.duplicate_rows,
        "missing_cells": profile.missing_cells,
        "numeric_columns": profile.numeric_columns[:10],
        "categorical_columns": profile.categorical_columns[:10],
        "numeric_stats": stats,
        "top_category_values": top_values,
        "date_range": date_range,
    }

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a senior business intelligence consultant writing the executive summary for a management dashboard report.\n\n"
                        "TASK: Produce a concise, insight-rich executive summary suitable for a PDF cover page.\n\n"
                        "RULES:\n"
                        "- headline: ONE sentence (max 20 words) capturing the main story with a specific number.\n"
                        "- findings: 4-5 bullet strings. Each must cite specific numbers from the stats provided. Be concrete.\n"
                        "- opportunities: 2-3 actionable recommendation strings (start with a verb like 'Investigate', 'Monitor', 'Prioritise').\n"
                        "- data_quality: ONE short sentence about data health (rows, duplicates, missing values).\n"
                        "- No generic observations. No markdown. Just the JSON object.\n\n"
                        "OUTPUT FORMAT — return ONLY valid JSON:\n"
                        '{"headline":"...","findings":["...","...","..."],"opportunities":["...","..."],"data_quality":"..."}'
                    ),
                },
                {"role": "user", "content": _json.dumps(payload)},
            ],
            temperature=0.15,
            stream=False,
            timeout=20,
        )
        content = ((response.choices[0].message.content) or "").strip()
        match = _re.search(r"\{.*\}", content, flags=_re.DOTALL)
        parsed = _json.loads(match.group(0) if match else content)
        return {
            "headline": str(parsed.get("headline", fallback["headline"])).strip()[:300],
            "findings": [str(f).strip() for f in (parsed.get("findings") or []) if str(f).strip()][:6],
            "opportunities": [str(o).strip() for o in (parsed.get("opportunities") or []) if str(o).strip()][:4],
            "data_quality": str(parsed.get("data_quality", fallback["data_quality"])).strip()[:400],
            "generated_at": generated_at,
        }
    except Exception:
        return fallback


def ai_clean_dataframe(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Clean a DataFrame using AI guidance when available, falling back to heuristics.

    Returns (cleaned_df, report) where report describes all cleaning actions taken.
    """
    import json as _json
    import re as _re

    report: dict = {
        "ai_powered": False,
        "actions": [],
        "rows_removed": 0,
        "columns_fixed": [],
        "missing_filled": {},
        "outliers_capped": {},
    }

    client, model = _get_ai_client()
    cleaning_plan: list[dict] = []

    if client is not None:
        profile = build_profile_summary(df)

        # Build richer statistical context for better AI recommendations
        outlier_info: dict = {}
        for col in profile.numeric_columns[:20]:
            try:
                q1 = float(df[col].quantile(0.25))
                q3 = float(df[col].quantile(0.75))
                iqr = q3 - q1
                lower_fence = q1 - 1.5 * iqr
                upper_fence = q3 + 1.5 * iqr
                outlier_count = int(((df[col] < lower_fence) | (df[col] > upper_fence)).sum())
                if outlier_count > 0:
                    outlier_info[col] = {"outlier_count": outlier_count, "iqr_lower": round(lower_fence, 4), "iqr_upper": round(upper_fence, 4)}
            except Exception:
                pass

        value_distributions: dict = {}
        for col in profile.categorical_columns[:15]:
            try:
                vc = df[col].value_counts(dropna=False)
                null_count = int(df[col].isna().sum())
                value_distributions[col] = {
                    "unique_values": int(df[col].nunique(dropna=True)),
                    "top_3": vc.head(3).index.astype(str).tolist(),
                    "missing": null_count,
                }
            except Exception:
                pass

        sample_info = {
            "columns": list(df.columns[:50]),
            "dtypes": {str(c): str(df[c].dtype) for c in df.columns[:50]},
            "missing_per_column": {str(c): int(df[c].isna().sum()) for c in df.columns[:50]},
            "total_rows": int(df.shape[0]),
            "duplicate_rows": int(df.duplicated().sum()),
            "numeric_columns": profile.numeric_columns[:30],
            "categorical_columns": profile.categorical_columns[:30],
            "numeric_stats": {
                str(c): {
                    "mean": round(float(df[c].mean()), 4),
                    "std": round(float(df[c].std()), 4),
                    "min": round(float(df[c].min()), 4),
                    "max": round(float(df[c].max()), 4),
                    "missing": int(df[c].isna().sum()),
                }
                for c in profile.numeric_columns[:20]
                if pd.api.types.is_numeric_dtype(df[c])
            },
            "outlier_analysis": outlier_info,
            "categorical_distributions": value_distributions,
            "sample_values": {
                str(c): df[c].dropna().head(5).astype(str).tolist()
                for c in df.columns[:25]
            },
        }
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a senior data engineer specializing in production-grade dataset cleaning for business intelligence.\n\n"
                            "TASK: Analyze the dataset profile and produce an optimal cleaning plan as a JSON array.\n\n"
                            "RULES:\n"
                            "- Each step must be actionable and justified by the actual data statistics provided.\n"
                            "- Prioritize steps by impact: data integrity issues first (duplicates, type fixes), then missing values, then outliers.\n"
                            "- Only recommend dropping columns if null_ratio > 0.7 OR if column has only 1 unique non-null value (zero variance).\n"
                            "- For fill_missing on numeric: prefer 'median' when std/mean > 0.5 (skewed data), else 'mean'.\n"
                            "- For cap_outliers: only apply when outlier_count > 2% of total rows; use 1-99 percentiles for heavy tails, 5-95 for moderate.\n"
                            "- For fix_dtype: detect date strings (ISO, MM/DD/YYYY, etc.) and numeric strings.\n"
                            "- Do NOT suggest cleaning for columns with 0 issues.\n\n"
                            "OUTPUT FORMAT — return ONLY a valid JSON array, no markdown, no explanation:\n"
                            '[\n'
                            '  {"action": "drop_duplicates", "column": null, "strategy": "exact match across all columns", "reason": "N exact duplicate rows inflate metrics"},\n'
                            '  {"action": "fill_missing", "column": "revenue", "strategy": "median", "fill_value": "median", "reason": "23 nulls; right-skewed distribution (std/mean=1.2) so median is robust"},\n'
                            '  {"action": "cap_outliers", "column": "price", "strategy": "IQR winsorizing", "percentile_low": 1, "percentile_high": 99, "reason": "47 outliers (3.1% of rows) beyond IQR fences"},\n'
                            '  {"action": "fix_dtype", "column": "order_date", "strategy": "parse as datetime", "reason": "column contains ISO date strings stored as object"},\n'
                            '  {"action": "drop_column", "column": "internal_id", "strategy": "remove zero-variance column", "reason": "only 1 unique value — no analytical value"}\n'
                            "]"
                        ),
                    },
                    {"role": "user", "content": _json.dumps(sample_info)},
                ],
                temperature=0.05,
                stream=False,
                timeout=20,
            )
            content = ((response.choices[0].message.content) or "").strip()
            match = _re.search(r"\[.*\]", content, flags=_re.DOTALL)
            cleaning_plan = _json.loads(match.group(0) if match else content)
            if isinstance(cleaning_plan, list):
                report["ai_powered"] = True
        except Exception:
            cleaning_plan = []

    # Build heuristic plan if AI did not provide one
    if not cleaning_plan:
        profile = build_profile_summary(df)
        if df.duplicated().sum() > 0:
            cleaning_plan.append({"action": "drop_duplicates", "column": None, "strategy": "drop exact duplicates", "reason": "Exact duplicate rows detected"})
        for col in profile.numeric_columns:
            missing = int(df[col].isna().sum())
            if missing > 0:
                cleaning_plan.append({"action": "fill_missing", "column": col, "strategy": "median", "fill_value": "median", "reason": f"{missing} missing values"})
        for col in profile.categorical_columns:
            missing = int(df[col].isna().sum())
            if missing > 0:
                cleaning_plan.append({"action": "fill_missing", "column": col, "strategy": "mode", "fill_value": "mode", "reason": f"{missing} missing values"})

    # Execute cleaning plan
    cleaned = df.copy()
    for step in cleaning_plan:
        action = str(step.get("action", "")).strip()
        col = step.get("column")
        try:
            if action == "drop_duplicates":
                before = len(cleaned)
                cleaned = cleaned.drop_duplicates()
                removed = before - len(cleaned)
                report["rows_removed"] += removed
                report["actions"].append(f"Removed {removed} duplicate rows")
            elif action == "fill_missing" and col and col in cleaned.columns:
                fill_value = str(step.get("fill_value", "median")).lower()
                missing_count = int(cleaned[col].isna().sum())
                if missing_count > 0:
                    if fill_value == "mean" and pd.api.types.is_numeric_dtype(cleaned[col]):
                        cleaned[col] = cleaned[col].fillna(cleaned[col].mean())
                    elif fill_value == "median" and pd.api.types.is_numeric_dtype(cleaned[col]):
                        cleaned[col] = cleaned[col].fillna(cleaned[col].median())
                    elif fill_value == "mode":
                        mode_vals = cleaned[col].mode()
                        if len(mode_vals) > 0:
                            cleaned[col] = cleaned[col].fillna(mode_vals[0])
                    else:
                        cleaned[col] = cleaned[col].fillna(fill_value)
                    report["missing_filled"][col] = missing_count
                    report["actions"].append(f"Filled {missing_count} missing values in '{col}' with {fill_value}")
                    if col not in report["columns_fixed"]:
                        report["columns_fixed"].append(col)
            elif action == "cap_outliers" and col and col in cleaned.columns:
                if pd.api.types.is_numeric_dtype(cleaned[col]):
                    pct_low = int(step.get("percentile_low", 1))
                    pct_high = int(step.get("percentile_high", 99))
                    lo = cleaned[col].quantile(pct_low / 100)
                    hi = cleaned[col].quantile(pct_high / 100)
                    before_count = int(((cleaned[col] < lo) | (cleaned[col] > hi)).sum())
                    cleaned[col] = cleaned[col].clip(lower=lo, upper=hi)
                    report["outliers_capped"][col] = before_count
                    report["actions"].append(f"Capped {before_count} outliers in '{col}' [{pct_low}th–{pct_high}th percentile]")
            elif action == "fix_dtype" and col and col in cleaned.columns:
                strategy = str(step.get("strategy", "")).lower()
                if "date" in strategy or "datetime" in strategy:
                    cleaned[col] = _to_datetime_safe(cleaned[col])
                    report["actions"].append(f"Converted '{col}' to datetime")
                elif "numeric" in strategy or "float" in strategy or "int" in strategy:
                    cleaned[col] = pd.to_numeric(cleaned[col], errors="coerce")
                    report["actions"].append(f"Converted '{col}' to numeric")
                if col not in report["columns_fixed"]:
                    report["columns_fixed"].append(col)
            elif action == "drop_column" and col and col in cleaned.columns:
                cleaned = cleaned.drop(columns=[col])
                report["actions"].append(f"Dropped column '{col}'")
        except Exception:
            pass

    if not report["actions"]:
        report["actions"].append("Data looks clean — no issues found")

    return cleaned, report


def ai_suggest_slicers(df: pd.DataFrame, profile: "ProfileSummary") -> list[dict]:
    """Use AI (or heuristics) to suggest the best slicer columns and filter types.

    Returns a list of dicts: {column, filter_type, label, reason}
    """
    import json as _json
    import re as _re

    client, model = _get_ai_client()

    if client is not None:
        payload = {
            "columns": list(df.columns[:60]),
            "dtypes": {str(c): str(df[c].dtype) for c in df.columns[:60]},
            "numeric_columns": profile.numeric_columns[:30],
            "categorical_columns": profile.categorical_columns[:30],
            "cardinality": {
                str(c): int(df[c].nunique(dropna=True))
                for c in profile.categorical_columns[:30]
            },
            "total_rows": profile.total_rows,
        }
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a senior BI engineer designing interactive dashboard filters for business users.\n\n"
                            "TASK: Recommend the best slicers/filters for this dataset. Return a JSON array of max 6 items.\n\n"
                            "SELECTION RULES:\n"
                            "- dropdown: categorical columns with 2–15 unique values (single-select, fast lookup)\n"
                            "- multiselect: categorical columns with 6–50 unique values (multi-value comparison)\n"
                            "- range: numeric/date columns for continuous narrowing\n"
                            "- Avoid ID columns (uuid, primary keys, row numbers) — they have no filter utility\n"
                            "- Prioritize columns that business users filter by most: time periods, regions, categories, status, segments\n"
                            "- Infer the business domain from column names to name slicers naturally\n\n"
                            "OUTPUT FORMAT — return ONLY a valid JSON array:\n"
                            '[\n'
                            '  {"column": "region", "filter_type": "dropdown", "label": "Region", "reason": "5 regions allow fast segment drill-down for executives"},\n'
                            '  {"column": "product_category", "filter_type": "multiselect", "label": "Product Category", "reason": "12 categories support cross-category comparison"},\n'
                            '  {"column": "revenue", "filter_type": "range", "label": "Revenue Range ($)", "reason": "Filter by revenue band to focus on high-value customers"}\n'
                            "]"
                        ),
                    },
                    {"role": "user", "content": _json.dumps(payload)},
                ],
                temperature=0.15,
                stream=False,
                timeout=12,
            )
            content = ((response.choices[0].message.content) or "").strip()
            match = _re.search(r"\[.*\]", content, flags=_re.DOTALL)
            suggestions = _json.loads(match.group(0) if match else content)
            if isinstance(suggestions, list) and suggestions:
                valid = []
                for s in suggestions:
                    col = str(s.get("column", "")).strip()
                    ft = str(s.get("filter_type", "dropdown")).strip()
                    if col in df.columns and ft in {"dropdown", "multiselect", "range"}:
                        valid.append({
                            "column": col,
                            "filter_type": ft,
                            "label": str(s.get("label", col)).strip()[:80],
                            "reason": str(s.get("reason", "")).strip()[:200],
                        })
                if valid:
                    return valid[:6], True
        except Exception:
            pass

    # Heuristic fallback
    suggestions = []
    for col in profile.categorical_columns:
        cardinality = int(df[col].nunique(dropna=True))
        if cardinality < 2:
            continue
        ft = "multiselect" if cardinality <= 20 else "dropdown"
        suggestions.append({
            "column": col,
            "filter_type": ft,
            "label": col.replace("_", " ").title(),
            "reason": f"Categorical column with {cardinality} unique values — good for filtering",
        })
        if len(suggestions) >= 4:
            break
    for col in profile.numeric_columns[:2]:
        suggestions.append({
            "column": col,
            "filter_type": "range",
            "label": col.replace("_", " ").title(),
            "reason": f"Numeric column — range filter allows narrowing the data",
        })
    return suggestions[:6], False


def ai_analyze_chart(chart_type: str, labels: list, values: list, title: str) -> str:
    """Generate AI-powered analysis text for a chart. Returns (insight_text, is_ai_powered)."""
    import json as _json

    client, model = _get_ai_client()
    if client is None:
        return _heuristic_chart_analysis(chart_type, labels, values, title), False

    numeric_vals = []
    for v in values[:50]:
        try:
            numeric_vals.append(float(v))
        except (TypeError, ValueError):
            pass

    stat_context: dict = {}
    if numeric_vals:
        total = sum(numeric_vals)
        avg = total / len(numeric_vals)
        sorted_idx = sorted(range(len(numeric_vals)), key=lambda x: -numeric_vals[x])
        stat_context = {
            "total": round(total, 2),
            "average": round(avg, 2),
            "max": round(max(numeric_vals), 2),
            "min": round(min(numeric_vals), 2),
            "max_label": labels[sorted_idx[0]] if labels and sorted_idx else "",
            "min_label": labels[sorted_idx[-1]] if labels and sorted_idx else "",
            "top_3_labels": [labels[i] for i in sorted_idx[:3] if i < len(labels)],
            "bottom_3_labels": [labels[i] for i in sorted_idx[-3:] if i < len(labels)],
            "data_points": len(numeric_vals),
            "spread_ratio": round(max(numeric_vals) / max(min(numeric_vals), 0.001), 1) if numeric_vals else 0,
        }
        if len(numeric_vals) >= 2:
            stat_context["trend_direction"] = "upward" if numeric_vals[-1] > numeric_vals[0] else "downward"
            pct_change = round((numeric_vals[-1] - numeric_vals[0]) / abs(numeric_vals[0]) * 100, 1) if numeric_vals[0] != 0 else 0
            stat_context["start_to_end_pct_change"] = pct_change
            # Detect volatility
            if len(numeric_vals) >= 4:
                import statistics as _st
                try:
                    cv = _st.stdev(numeric_vals) / avg * 100 if avg != 0 else 0
                    stat_context["coefficient_of_variation_pct"] = round(cv, 1)
                except Exception:
                    pass
        if total > 0 and chart_type in ("pie", "doughnut", "polararea"):
            stat_context["top_pct_of_total"] = round(max(numeric_vals) / total * 100, 1)
            if len(numeric_vals) >= 2:
                stat_context["top2_combined_pct"] = round(sum(sorted(numeric_vals, reverse=True)[:2]) / total * 100, 1)
        # Above/below average count
        stat_context["above_avg_count"] = sum(1 for v in numeric_vals if v > avg)

    payload = {
        "chart_type": chart_type,
        "title": title,
        "labels": labels[:40],
        "values": [round(float(v), 2) if isinstance(v, (int, float)) else v for v in values[:40]],
        "statistics": stat_context,
    }
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a sharp business data analyst writing executive-level chart commentary for a professional dashboard report.\n\n"
                        "TASK: Write 2-3 punchy, numerically specific insights for the chart. "
                        "Use pre-computed statistics to cite precise values, labels, and percentages.\n\n"
                        "RULES:\n"
                        "- Plain text only — no markdown, no bullet points, no headers.\n"
                        "- Every sentence must contain at least one specific number or label from the data.\n"
                        "- bar/hbar: highlight top performer, gap between top and bottom, count above average.\n"
                        "- line/area: trend direction, peak period, magnitude of change, any reversal points.\n"
                        "- pie/doughnut/polararea: dominant segment share, top-2 combined share, smallest segment.\n"
                        "- scatter/bubble: correlation direction, any visible cluster or outlier.\n"
                        "- kpi: compare to average, contextualise the magnitude.\n"
                        "- End with ONE forward-looking sentence: what to watch or action to take.\n"
                        "- Keep total under 120 words. Be direct, confident, and specific."
                    ),
                },
                {"role": "user", "content": _json.dumps(payload)},
            ],
            temperature=0.15,
            stream=False,
            timeout=15,
        )
        return ((response.choices[0].message.content) or "").strip(), True
    except Exception:
        return _heuristic_chart_analysis(chart_type, labels, values, title), False


def _heuristic_chart_analysis(chart_type: str, labels: list, values: list, title: str) -> str:
    """Simple heuristic-based chart insight when AI is unavailable."""
    if not values or not labels:
        return f"No data available for analysis of '{title}'."
    numeric_values = []
    for v in values:
        try:
            numeric_values.append(float(v))
        except (TypeError, ValueError):
            pass
    if not numeric_values:
        return f"'{title}' contains {len(labels)} categories."
    total = sum(numeric_values)
    max_val = max(numeric_values)
    min_val = min(numeric_values)
    max_label = labels[numeric_values.index(max_val)] if labels else ""
    avg_val = total / len(numeric_values)
    if chart_type in ("pie", "doughnut") and total > 0:
        pct = round(max_val / total * 100, 1)
        return (
            f"'{max_label}' dominates with {pct}% of the total. "
            f"The chart shows {len(labels)} categories with a combined total of {total:,.0f}."
        )
    if chart_type in ("bar", "hbar"):
        return (
            f"Top category is '{max_label}' ({max_val:,.0f}). "
            f"Average across {len(numeric_values)} groups: {avg_val:,.1f}. "
            f"Range: {min_val:,.0f} – {max_val:,.0f}."
        )
    if chart_type == "line":
        trend = "upward" if numeric_values[-1] > numeric_values[0] else "downward"
        return (
            f"The trend is {trend} overall. "
            f"Started at {numeric_values[0]:,.0f}, ended at {numeric_values[-1]:,.0f}. "
            f"Peak value: {max_val:,.0f}."
        )
    return f"'{title}' — {len(numeric_values)} data points, total {total:,.0f}, avg {avg_val:,.1f}."


def ai_generate_dashboard_specs(df: pd.DataFrame, profile: "ProfileSummary") -> list[dict] | None:
    """Ask AI to design a schema-agnostic, comprehensive dashboard plan and normalize it to widget specs.

    Returns a list of widget specs or None if AI is unavailable.
    Each spec includes an 'ai_insight' field with a data-driven analytical insight.
    Narrative and section headings are injected at appropriate positions.
    """
    import json as _json
    import re as _re
    from django.conf import settings

    client, model = _get_ai_client()
    if client is None:
        return None
    specs_timeout = int(getattr(settings, "DEEPSEEK_SPECS_TIMEOUT", 60))

    date_cols = [c for c in df.columns if any(k in str(c).lower() for k in ["date", "month", "year", "period", "quarter"])]

    # Compute rich statistical context for AI
    sample_stats: dict = {}
    for col in profile.numeric_columns[:12]:
        try:
            s = df[col].dropna()
            sample_stats[str(col)] = {
                "sum": round(float(s.sum()), 2),
                "mean": round(float(s.mean()), 2),
                "median": round(float(s.median()), 2),
                "std": round(float(s.std()), 2),
                "min": round(float(s.min()), 2),
                "max": round(float(s.max()), 2),
                "human_label": _humanize_col(col),
            }
        except Exception:
            pass

    categorical_cardinality: dict[str, int] = {}
    categorical_top_values: dict[str, dict] = {}
    for col in profile.categorical_columns[:20]:
        try:
            cardinality = int(df[col].nunique(dropna=True))
            categorical_cardinality[str(col)] = cardinality
            top_vals = df[col].value_counts().head(5)
            categorical_top_values[str(col)] = {str(k): int(v) for k, v in top_vals.items()}
        except Exception:
            pass

    null_rate: dict[str, float] = {}
    for col in list(df.columns[:30]):
        try:
            null_rate[str(col)] = round(float(df[col].isna().mean() * 100), 2)
        except Exception:
            pass

    date_ranges: dict = {}
    for col in date_cols[:3]:
        try:
            tmp = _to_datetime_safe(df[col]).dropna()
            if len(tmp) > 0:
                date_ranges[str(col)] = {
                    "min": str(tmp.min().date()),
                    "max": str(tmp.max().date()),
                    "span_days": int((tmp.max() - tmp.min()).days),
                }
        except Exception:
            pass

    summary_rows = []
    try:
        summary_rows = df.head(8).fillna("").astype(str).to_dict(orient="records")
    except Exception:
        summary_rows = []

    mode = "analytical"
    if date_cols:
        mode = "executive"
    if profile.total_rows and int(profile.total_rows) > 150000:
        mode = "operational"

    payload = {
        "columns": [str(c) for c in df.columns[:60]],
        "numeric_columns": [str(c) for c in profile.numeric_columns[:20]],
        "categorical_columns": [str(c) for c in profile.categorical_columns[:20]],
        "date_columns": [str(c) for c in date_cols[:5]],
        "sample_rows": summary_rows,
        "total_rows": int(profile.total_rows),
        "duplicate_rows": int(profile.duplicate_rows),
        "missing_cells": int(profile.missing_cells),
        "sample_stats": sample_stats,
        "categorical_cardinality": categorical_cardinality,
        "categorical_top_values": categorical_top_values,
        "date_ranges": date_ranges,
        "null_rate_pct": null_rate,
        "allowed_chart_types": [
            "kpi", "bar", "line", "area", "pie", "doughnut", "hbar", "scatter", "radar", "table",
            "bubble", "polararea", "mixed", "funnel", "gauge", "waterfall",
        ],
        "allowed_sizes": ["sm", "md", "lg"],
        "allowed_palettes": ["indigo", "blue", "emerald", "rose", "amber", "vibrant", "ocean", "sunset"],
        "mode": mode,
    }

    def _normalize_plan_to_specs(plan: object) -> list[dict]:
        if isinstance(plan, list):
            return plan
        if not isinstance(plan, dict):
            return []

        insights = [str(x).strip() for x in (plan.get("insights") or []) if str(x).strip()]
        specs: list[dict] = []

        # ── Narrative text canvas at position 0 ────────────────────────────
        narrative = str(plan.get("narrative") or "").strip()
        if narrative:
            specs.append({
                "title": "AI Dashboard Summary",
                "chart_type": "text_canvas",
                "dimension": None,
                "measures": [],
                "size": "lg",
                "palette": "indigo",
                "ai_insight": "",
                "_narrative_content": narrative,
                "_is_narrative": True,
            })

        # ── KPI section ────────────────────────────────────────────────────
        kpi_heading = str(plan.get("kpi_section_title") or "Key Performance Indicators").strip()
        kpis = [k for k in (plan.get("kpis") or []) if isinstance(k, dict)]
        if kpis:
            specs.append({
                "title": kpi_heading,
                "chart_type": "heading",
                "dimension": None,
                "measures": [],
                "size": "lg",
                "palette": "indigo",
                "ai_insight": "",
                "_heading_color": "indigo",
                "_heading_font_size": "xl",
            })
        for i, kpi in enumerate(kpis):
            name = str(kpi.get("name", "")).strip() or f"KPI {i + 1}"
            value_col = str(kpi.get("measure") or kpi.get("column") or "").strip()
            _raw_change = kpi.get("change")
            change = "" if (_raw_change is None or str(_raw_change).strip().lower() in ("", "none", "null", "n/a", "na")) else str(_raw_change).strip()
            insight = str(kpi.get("insight", "")).strip() or (insights[i % len(insights)] if insights else "")
            full_insight = f"{insight} Period change: {change}." if change and insight else insight
            _agg = str(kpi.get("agg") or "").strip().lower()
            spec: dict = {
                "title": name,
                "chart_type": "kpi",
                "dimension": None,
                "measures": [value_col] if value_col else [],
                "size": "sm",
                "palette": "indigo",
                "ai_insight": full_insight[:400],
            }
            if _agg in ("sum", "avg", "count", "nunique", "max", "min"):
                spec["_agg"] = _agg
            specs.append(spec)

        # ── Charts section ─────────────────────────────────────────────────
        charts = [c for c in (plan.get("charts") or []) if isinstance(c, dict)]
        chart_heading = str(plan.get("chart_section_title") or "Performance Analysis").strip()
        if charts:
            specs.append({
                "title": chart_heading,
                "chart_type": "heading",
                "dimension": None,
                "measures": [],
                "size": "lg",
                "palette": "blue",
                "ai_insight": "",
                "_heading_color": "blue",
                "_heading_font_size": "xl",
            })
        for i, chart in enumerate(charts):
            chart_type = str(chart.get("type", "bar")).strip().lower()
            title = str(chart.get("title", "")).strip() or f"Chart {i + 1}"
            dimension = str(chart.get("x") or chart.get("dimension") or "").strip()
            y = chart.get("y") or chart.get("measure") or chart.get("measures") or []
            measures = [y] if isinstance(y, str) else (list(y) if isinstance(y, list) else [])
            measures = [str(m).strip() for m in measures if str(m).strip()]
            x_measure = str(chart.get("x_measure") or "").strip()
            y_measure = str(chart.get("y_measure") or "").strip()
            insight = str(chart.get("insight", "")).strip() or (insights[i % len(insights)] if insights else "")
            specs.append({
                "title": title,
                "chart_type": chart_type,
                "dimension": dimension,
                "measures": measures,
                "x_measure": x_measure,
                "y_measure": y_measure,
                "size": str(chart.get("size") or "md").strip().lower(),
                "palette": str(chart.get("palette") or "indigo").strip().lower(),
                "ai_insight": insight[:400],
            })

        # ── Tables section ─────────────────────────────────────────────────
        tables = [t for t in (plan.get("tables") or []) if isinstance(t, dict)]
        table_heading = str(plan.get("table_section_title") or "Data Details").strip()
        if tables:
            specs.append({
                "title": table_heading,
                "chart_type": "heading",
                "dimension": None,
                "measures": [],
                "size": "lg",
                "palette": "slate",
                "ai_insight": "",
                "_heading_color": "slate",
                "_heading_font_size": "xl",
            })
        for i, table in enumerate(tables):
            title = str(table.get("title", "")).strip() or f"Table {i + 1}"
            cols = table.get("columns") or []
            measures = [str(c).strip() for c in cols if str(c).strip()]
            insight = str(table.get("insight", "")).strip() or (insights[i % len(insights)] if insights else "")
            specs.append({
                "title": title,
                "chart_type": "table",
                "dimension": "",
                "measures": measures,
                "size": "lg",
                "palette": "slate",
                "ai_insight": insight[:400],
            })

        return specs

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a world-class Senior BI Dashboard Architect at a top-tier global analytics consultancy. "
                        "Your dashboards are used by Fortune 500 CEOs, investment committees, and government ministers. "
                        "They are modern, insight-dense, narratively coherent, and built for confident executive decision-making.\n\n"
                        "Create a COMPREHENSIVE, schema-agnostic BI dashboard plan for the provided dataset.\n"
                        "Mode is provided in payload: executive | analytical | operational.\n\n"
                        "═══ NAMING RULES (non-negotiable) ═══\n"
                        "- KPI names: 2-4 business-friendly words. 'total_revenue_usd' → 'Total Revenue'. "
                        "'num_orders' → 'Orders'. 'avg_order_value' → 'Avg Order Value'.\n"
                        "- Chart titles: Write a business question being answered. "
                        "GOOD: 'Revenue by Region', 'Monthly Growth Trend', 'Top 10 Products by Margin'. "
                        "BAD: 'sales_amount by region_name', 'chart of qty'.\n"
                        "- Insights: Lead with the finding, cite a specific number. "
                        "GOOD: 'North America drives 42% of total revenue at $2.1M — the largest regional contributor.' "
                        "BAD: 'region column shows high sales_amount values'.\n\n"
                        "═══ NARRATIVE RULES ═══\n"
                        "- 'narrative': Write a 2-3 sentence executive summary of what this dashboard reveals. "
                        "Must cite the most important metric value from sample_stats. "
                        "Structured as: [What the data is about] + [Key finding with number] + [Strategic implication].\n"
                        "- 'kpi_section_title': A section title like 'Executive KPI Summary' or 'Key Business Metrics'\n"
                        "- 'chart_section_title': A section title like 'Performance Deep-Dive' or 'Trend & Distribution Analysis'\n"
                        "- 'table_section_title': A section title like 'Transaction Detail' or 'Raw Data Explorer'\n\n"
                        "═══ KPI RULES ═══\n"
                        "- Generate 4-6 KPIs. Cover: volume metric, financial/value metric, rate/ratio, count, "
                        "and growth metric (if dates present). For distinct entity counts (unique suppliers, customers, products), "
                        "use a categorical column as 'measure' and set agg='nunique'.\n"
                        "- 'measure' must be an exact column name from provided columns (numeric or categorical).\n"
                        "- 'insight': 1 sentence citing the actual sum or mean from sample_stats. "
                        "Example: 'Total revenue of $4.2M represents a strong baseline; focus on high-margin segments.'\n"
                        "- 'change': Describe a meaningful comparison (e.g. '12% above Q3 average', 'top 20% of performers').\n\n"
                        "═══ CHART SELECTION RULES ═══\n"
                        "- Date column present → ALWAYS a line chart for primary trend + area for secondary metric.\n"
                        "- Category (cardinality 3-12) + numeric → bar. Title: '[Metric] by [Category]'\n"
                        "- Category (cardinality >12) + numeric → hbar top 10. Title: 'Top 10 [Category] by [Metric]'\n"
                        "- Part-to-whole (cardinality ≤8) → doughnut. Title: '[Metric] Mix by [Category]'\n"
                        "- Two numerics with correlation → scatter. Title: '[Metric A] vs [Metric B] Relationship'\n"
                        "- Multiple categories + numeric → radar. Title: '[Metric] Profile by [Category]'\n"
                        "- Stage/funnel progression → funnel. Title: '[Process] Conversion Funnel'\n"
                        "- Cumulative variance → waterfall. Title: '[Metric] Waterfall by [Category]'\n"
                        "- Time series + rate overlay → mixed. Title: '[Metric] & [Rate] by [Period]'\n"
                        "- Generate 6-10 charts covering different analytical angles.\n\n"
                        "═══ SIZE RULES ═══\n"
                        "kpi='sm', line/area/waterfall='lg', bar/hbar/mixed='md', pie/doughnut/radar/scatter='md', "
                        "table='lg', funnel='md'.\n\n"
                        "═══ PALETTE RULES ═══\n"
                        "indigo=KPIs/neutral, emerald=revenue/growth/positive, rose=loss/churn/negative/risk, "
                        "ocean=time-series/trends, blue=secondary trends, amber=distribution/ranking, "
                        "vibrant=multi-category/comparison, sunset=relationships/scatter.\n\n"
                        "═══ INSIGHT QUALITY RULES ═══\n"
                        "- EVERY insight must cite at least one specific number from sample_stats or cardinality.\n"
                        "- Structure: [Key finding with number] → [Business implication] → [Recommended action].\n"
                        "- Avoid generic phrases like 'shows trends' or 'indicates patterns'.\n"
                        "- Maximum 120 words per insight. Be punchy and decisive.\n\n"
                        "Return ONLY valid JSON with these exact keys:\n"
                        "{"
                        "\"narrative\":\"2-3 sentence executive summary with specific numbers\","
                        "\"kpi_section_title\":\"Section heading for KPIs\","
                        "\"chart_section_title\":\"Section heading for charts\","
                        "\"table_section_title\":\"Section heading for tables\","
                        "\"schema\":{\"columns\":[],\"types\":{}},"
                        "\"kpis\":[{\"name\":\"Business Name\",\"measure\":\"exact_column\",\"agg\":\"sum|avg|count|nunique\",\"change\":\"comparison or null\",\"insight\":\"data-driven sentence\"}],"
                        "\"charts\":[{\"type\":\"chart_type\",\"title\":\"Business Question Title\",\"x\":\"exact_column\",\"y\":[\"exact_column\"],\"x_measure\":\"exact_col_or_empty\",\"y_measure\":\"exact_col_or_empty\",\"size\":\"md\",\"palette\":\"indigo\",\"insight\":\"data-driven sentence with number\"}],"
                        "\"tables\":[{\"title\":\"Analytical View Name\",\"columns\":[\"exact_col\"],\"insight\":\"data-driven sentence\"}],"
                        "\"insights\":[\"Global finding 1\",\"Global finding 2\",\"Global finding 3\"]"
                        "}\n"
                        "CRITICAL: Use EXACT column names from the payload for x/y/measure fields. "
                        "Chart titles, KPI names, and insights must be human-readable and numerically specific. "
                        "No markdown. No extra text outside the JSON."
                    ),
                },
                {"role": "user", "content": _json.dumps(payload)},
            ],
            temperature=0.15,
            stream=False,
            timeout=specs_timeout,
        )
        content = ((response.choices[0].message.content) or "").strip()
        if content.startswith("["):
            match_arr = _re.search(r"\[.*\]", content, flags=_re.DOTALL)
            parsed = _json.loads(match_arr.group(0) if match_arr else content)
        else:
            match_obj = _re.search(r"\{.*\}", content, flags=_re.DOTALL)
            parsed = _json.loads(match_obj.group(0) if match_obj else content)
        specs = _normalize_plan_to_specs(parsed)
        if specs:
            return specs
        logger.warning("DeepSeek returned a response but produced no normalizable dashboard specs.")
    except Exception:
        logger.exception(
            "DeepSeek dashboard specs generation failed (timeout=%ss); falling back to heuristic specs.",
            specs_timeout,
        )
    return None


def ai_generate_dashboard_title(df: pd.DataFrame, profile: "ProfileSummary", dataset_name: str = "") -> str | None:
    """Ask AI for a concise dashboard title tailored to the dataset."""
    import json as _json
    import re as _re

    client, model = _get_ai_client()
    if client is None:
        return None

    date_cols = [c for c in df.columns if any(k in str(c).lower() for k in ["date", "month", "year", "period", "quarter"])]
    payload = {
        "dataset_name": str(dataset_name or "").strip(),
        "columns": [str(c) for c in df.columns[:50]],
        "numeric_columns": [str(c) for c in profile.numeric_columns[:12]],
        "categorical_columns": [str(c) for c in profile.categorical_columns[:12]],
        "date_columns": [str(c) for c in date_cols[:5]],
        "total_rows": int(profile.total_rows),
    }
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert analytics storyteller. Generate ONE concise executive dashboard title.\n"
                        "Return ONLY valid JSON with key: title.\n"
                        "Rules:\n"
                        "- Keep title 3-7 words.\n"
                        "- Be specific to the dataset context and columns.\n"
                        "- Avoid generic words like 'Overview' when possible.\n"
                        "- No punctuation-heavy or clickbait phrasing."
                    ),
                },
                {"role": "user", "content": _json.dumps(payload)},
            ],
            temperature=0.2,
            stream=False,
            timeout=12,
        )
        content = ((response.choices[0].message.content) or "").strip()
        match = _re.search(r"\{.*\}", content, flags=_re.DOTALL)
        parsed = _json.loads(match.group(0) if match else content)
        title = str(parsed.get("title", "")).strip()
        if title:
            return title[:200]
    except Exception:
        pass
    return None


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

    version_id = dataset_version.id

    def _make_builder(dimension="", measures=None, measure="", x_measure="", y_measure="", palette="indigo"):
        return {
            "dimension": dimension,
            "measures": measures or [],
            "measure": measure,
            "x_measure": x_measure,
            "y_measure": y_measure,
            "x_label": "",
            "y_label": "",
            "palette": palette,
            "tooltip_enabled": True,
            "table_columns": [],
            "group_by": [],
            "dataset_version_id": version_id,
        }

    date_cols = [
        c for c in df.columns
        if any(k in str(c).lower() for k in ["date", "month", "year", "period", "quarter"])
    ]

    # ── KPI 1: Total rows ────────────────────────────────────────────────────
    kpi_rows_cfg: dict = {
        "kpi": "Total Records",
        "value": f"{profile.total_rows:,}",
        "kpi_meta": {"format": "count", "icon": "people"},
        "layout": {"size": "sm"},
    }
    kpi_rows_cfg["builder"] = _make_builder(measure="rows")
    specs.append({"title": "Total Records", "widget_type": "kpi", "config": kpi_rows_cfg, "position": position})
    position += 1

    # ── KPI 2: Sum of first numeric column ──────────────────────────────────
    if profile.suggested_measures:
        m1 = profile.suggested_measures[0]
        try:
            total = df[m1].sum()
            trend_data = _compute_kpi_trend(df, m1)
            kpi_meta = _detect_kpi_meta(m1)
            human_m1 = _humanize_col(m1)
            kpi_cfg: dict = {
                "kpi": human_m1,
                "value": f"{total:,.0f}",
                "kpi_meta": kpi_meta,
                "layout": {"size": "sm"},
            }
            if trend_data:
                kpi_cfg["trend"] = trend_data
            kpi_cfg["builder"] = _make_builder(measures=[m1], measure=m1)
            specs.append({"title": f"Total {human_m1}", "widget_type": "kpi", "config": kpi_cfg, "position": position})
            position += 1
        except Exception:
            pass

    # ── KPI 3: Sum of second numeric column (or average of first) ───────────
    if len(profile.suggested_measures) >= 2:
        m2 = profile.suggested_measures[1]
        try:
            total2 = df[m2].sum()
            trend_data2 = _compute_kpi_trend(df, m2)
            kpi_meta2 = _detect_kpi_meta(m2)
            human_m2 = _humanize_col(m2)
            kpi2_cfg: dict = {
                "kpi": human_m2,
                "value": f"{total2:,.0f}",
                "kpi_meta": kpi_meta2,
                "layout": {"size": "sm"},
            }
            if trend_data2:
                kpi2_cfg["trend"] = trend_data2
            kpi2_cfg["builder"] = _make_builder(measures=[m2], measure=m2)
            specs.append({"title": f"Total {human_m2}", "widget_type": "kpi", "config": kpi2_cfg, "position": position})
            position += 1
        except Exception:
            pass
    elif profile.suggested_measures:
        m1 = profile.suggested_measures[0]
        try:
            avg_val = df[m1].mean()
            kpi_meta_avg = _detect_kpi_meta(m1)
            human_m1 = _humanize_col(m1)
            avg_cfg: dict = {
                "kpi": human_m1,
                "value": f"{avg_val:,.2f}",
                "kpi_meta": kpi_meta_avg,
                "layout": {"size": "sm"},
            }
            avg_cfg["builder"] = _make_builder(measures=[m1], measure=m1)
            specs.append({"title": f"Avg {human_m1}", "widget_type": "kpi", "config": avg_cfg, "position": position})
            position += 1
        except Exception:
            pass

    # ── KPI 4: Third numeric if available ──────────────────────────────────
    if len(profile.suggested_measures) >= 3:
        m3 = profile.suggested_measures[2]
        try:
            total3 = df[m3].sum()
            trend_data3 = _compute_kpi_trend(df, m3)
            kpi_meta3 = _detect_kpi_meta(m3)
            human_m3 = _humanize_col(m3)
            kpi3_cfg: dict = {
                "kpi": human_m3,
                "value": f"{total3:,.0f}",
                "kpi_meta": kpi_meta3,
                "layout": {"size": "sm"},
            }
            if trend_data3:
                kpi3_cfg["trend"] = trend_data3
            kpi3_cfg["builder"] = _make_builder(measures=[m3], measure=m3)
            specs.append({"title": f"Total {human_m3}", "widget_type": "kpi", "config": kpi3_cfg, "position": position})
            position += 1
        except Exception:
            pass

    # ── Chart 1: Bar – top dimension by first measure ────────────────────────
    if profile.suggested_dimensions and profile.suggested_measures:
        dim = profile.suggested_dimensions[0]
        measure = profile.suggested_measures[0]
        try:
            top = df.groupby(dim)[measure].sum().nlargest(10)
            bar_cfg = _bar_config([str(l) for l in top.index], [round(float(v), 2) for v in top.values], measure, "indigo")
            bar_cfg["layout"] = {"size": "md"}
            bar_cfg["builder"] = _make_builder(dimension=dim, measures=[measure], measure=measure)
            title = f"{_humanize_col(measure)} by {_humanize_col(dim)}"
            specs.append({"title": title, "widget_type": "bar", "config": bar_cfg, "position": position})
            position += 1
        except Exception:
            pass

    # ── Chart 2: Line – time series ──────────────────────────────────────────
    if date_cols and profile.suggested_measures:
        date_col = date_cols[0]
        measure = profile.suggested_measures[0]
        try:
            tmp = df[[date_col, measure]].copy()
            tmp[date_col] = _to_datetime_safe(tmp[date_col])
            tmp = tmp.dropna(subset=[date_col])
            trend = tmp.groupby(tmp[date_col].dt.to_period("M"))[measure].sum()
            if len(trend) >= 2:
                line_cfg = _line_config([str(p) for p in trend.index], [round(float(v), 2) for v in trend.values], measure, "blue")
                line_cfg["layout"] = {"size": "lg"}
                line_cfg["builder"] = _make_builder(dimension=date_col, measures=[measure], measure=measure)
                title = f"{_humanize_col(measure)} Trend Over Time"
                specs.append({"title": title, "widget_type": "line", "config": line_cfg, "position": position})
                position += 1
        except Exception:
            pass

    # ── Chart 3: Area – time series (second measure or same) ────────────────
    if date_cols and len(profile.suggested_measures) >= 2:
        date_col = date_cols[0]
        measure = profile.suggested_measures[1]
        try:
            tmp = df[[date_col, measure]].copy()
            tmp[date_col] = _to_datetime_safe(tmp[date_col])
            tmp = tmp.dropna(subset=[date_col])
            trend = tmp.groupby(tmp[date_col].dt.to_period("M"))[measure].sum()
            if len(trend) >= 2:
                area_cfg = _area_config([str(p) for p in trend.index], [round(float(v), 2) for v in trend.values], measure, "emerald")
                area_cfg["layout"] = {"size": "lg"}
                area_cfg["builder"] = _make_builder(dimension=date_col, measures=[measure], measure=measure)
                title = f"{_humanize_col(measure)} Monthly Trend"
                specs.append({"title": title, "widget_type": "area", "config": area_cfg, "position": position})
                position += 1
        except Exception:
            pass

    # ── Chart 4: Pie – distribution of first categorical dimension ───────────
    if profile.suggested_dimensions:
        dim = profile.suggested_dimensions[0]
        try:
            vc = df[dim].value_counts().head(6)
            pie_cfg = _pie_config([str(l) for l in vc.index], [int(v) for v in vc.values], "vibrant")
            pie_cfg["layout"] = {"size": "md"}
            pie_cfg["builder"] = _make_builder(dimension=dim)
            title = f"{_humanize_col(dim)} Share"
            specs.append({"title": title, "widget_type": "pie", "config": pie_cfg, "position": position})
            position += 1
        except Exception:
            pass

    # ── Chart 5: Doughnut – distribution of second categorical dimension ─────
    if len(profile.suggested_dimensions) >= 2:
        dim2 = profile.suggested_dimensions[1]
        try:
            vc2 = df[dim2].value_counts().head(6)
            doughnut_cfg = _doughnut_config([str(l) for l in vc2.index], [int(v) for v in vc2.values], "ocean")
            doughnut_cfg["layout"] = {"size": "md"}
            doughnut_cfg["builder"] = _make_builder(dimension=dim2)
            title = f"{_humanize_col(dim2)} Breakdown"
            specs.append({"title": title, "widget_type": "doughnut", "config": doughnut_cfg, "position": position})
            position += 1
        except Exception:
            pass

    # ── Chart 6: Horizontal Bar – ranking of second dimension ────────────────
    if len(profile.suggested_dimensions) > 1 and profile.suggested_measures:
        dim2 = profile.suggested_dimensions[1]
        measure = profile.suggested_measures[0]
        try:
            top2 = df.groupby(dim2)[measure].sum().nlargest(10)
            hbar_cfg = _hbar_config([str(l) for l in top2.index], [round(float(v), 2) for v in top2.values], measure, "amber")
            hbar_cfg["layout"] = {"size": "md"}
            hbar_cfg["builder"] = _make_builder(dimension=dim2, measures=[measure], measure=measure)
            title = f"Top {_humanize_col(dim2)} by {_humanize_col(measure)}"
            specs.append({"title": title, "widget_type": "hbar", "config": hbar_cfg, "position": position})
            position += 1
        except Exception:
            pass

    # ── Chart 7: Scatter – correlation of two numeric columns ────────────────
    if len(profile.suggested_measures) >= 2:
        x_col = profile.suggested_measures[0]
        y_col = profile.suggested_measures[1]
        try:
            tmp = df[[x_col, y_col]].dropna().head(500)
            scatter_cfg = _scatter_config(
                [round(float(v), 4) for v in tmp[x_col]],
                [round(float(v), 4) for v in tmp[y_col]],
                x_col, y_col, "rose", f"{_humanize_col(x_col)} vs {_humanize_col(y_col)}",
            )
            scatter_cfg["layout"] = {"size": "md"}
            scatter_cfg["builder"] = _make_builder(x_measure=x_col, y_measure=y_col)
            title = f"{_humanize_col(x_col)} vs {_humanize_col(y_col)} Correlation"
            specs.append({"title": title, "widget_type": "scatter", "config": scatter_cfg, "position": position})
            position += 1
        except Exception:
            pass

    # ── Chart 8: Radar – multi-dimension comparison ──────────────────────────
    if profile.suggested_dimensions and profile.suggested_measures:
        dim = profile.suggested_dimensions[0]
        measure = profile.suggested_measures[0]
        try:
            top_r = df.groupby(dim)[measure].sum().nlargest(8)
            if len(top_r) >= 3:
                radar_cfg = _radar_config([str(l) for l in top_r.index], [round(float(v), 2) for v in top_r.values], measure, "sunset")
                radar_cfg["layout"] = {"size": "md"}
                radar_cfg["builder"] = _make_builder(dimension=dim, measures=[measure], measure=measure)
                title = f"{_humanize_col(measure)} Performance by {_humanize_col(dim)}"
                specs.append({"title": title, "widget_type": "radar", "config": radar_cfg, "position": position})
                position += 1
        except Exception:
            pass

    # ── Chart 9: Data Table ──────────────────────────────────────────────────
    try:
        table_cols = (profile.categorical_columns[:2] + profile.numeric_columns[:3])[:5]
        if not table_cols:
            table_cols = [str(c) for c in df.columns[:5]]
        preview = df[table_cols].head(50).fillna("")
        rows = [[str(v) for v in row] for row in preview.values.tolist()]
        table_cfg: dict = {"columns": table_cols, "rows": rows, "layout": {"size": "lg"}}
        table_cfg["builder"] = _make_builder(measures=profile.numeric_columns[:3])
        specs.append({"title": "Detailed Data View", "widget_type": "table", "config": table_cfg, "position": position})
        position += 1
    except Exception:
        pass

    # ── Chart 10: Second bar – third dimension (if available) ────────────────
    if len(profile.suggested_dimensions) > 2 and profile.suggested_measures:
        dim3 = profile.suggested_dimensions[2]
        measure = profile.suggested_measures[0]
        try:
            top3 = df.groupby(dim3)[measure].sum().nlargest(8)
            bar3_cfg = _bar_config([str(l) for l in top3.index], [round(float(v), 2) for v in top3.values], measure, "slate")
            bar3_cfg["layout"] = {"size": "md"}
            bar3_cfg["builder"] = _make_builder(dimension=dim3, measures=[measure], measure=measure)
            title = f"{_humanize_col(measure)} by {_humanize_col(dim3)}"
            specs.append({"title": title, "widget_type": "bar", "config": bar3_cfg, "position": position})
            position += 1
        except Exception:
            pass

    return specs
