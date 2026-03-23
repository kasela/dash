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
    # Detailed semantic type per column: currency|percentage|number|text|date|boolean|id|category
    column_types: dict = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.column_types is None:
            self.column_types = {}


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


def detect_column_types(df: pd.DataFrame) -> dict[str, dict]:
    """Detect detailed semantic type for every column in the DataFrame.

    Semantic types (mutually exclusive, in priority order):
      date       – temporal: datetime dtype OR string parseable as date
      boolean    – true/false or yes/no or 0/1 with ≤2-3 unique values
      id         – surrogate keys / UUIDs with very high uniqueness ratio
      currency   – monetary numeric (name keywords OR actual $ £ € ¥ signs in data)
      percentage – ratio/rate numeric (name keywords OR values 0-100 with pct signal)
      number     – generic numeric (int or float)
      category   – low-to-medium cardinality strings (≤50 unique values)
      text       – high cardinality strings / free text

    Returns dict: {col_name: {"semantic_type": str, "cardinality": int,
                               "null_pct": float, "sample_values": list}}
    """
    import re as _re

    result: dict[str, dict] = {}
    n = max(len(df), 1)

    _CURRENCY_NAME_KWS = [
        "revenue", "sales", "profit", "cost", "price", "amount", "income",
        "spend", "budget", "earning", "margin", "value", "gmv", "arpu", "ltv",
        "fee", "payment", "invoice", "cash", "dollar", "usd", "eur", "gbp",
        "sgd", "aud", "jpy", "inr", "cny", "lkr", "chf", "cad",
    ]
    _PCT_NAME_KWS = [
        "rate", "ratio", "pct", "percent", "share", "growth", "churn",
        "conversion", "efficiency", "utilization", "retention", "accuracy",
        "discount", "yield", "margin_pct", "markup", "tax_rate",
    ]
    _DATE_NAME_KWS = [
        "date", "time", "timestamp", "created", "updated", "modified",
        "month", "year", "quarter", "period", "week", "day",
    ]
    _ID_NAME_KWS = [
        "_id", "id_", "_key", "_uuid", "_ref", "_pk", "_no",
        "rowid", "row_id", "record_id", "index",
    ]
    _BOOL_VALUES = {
        frozenset({"true", "false"}), frozenset({"yes", "no"}),
        frozenset({"1", "0"}), frozenset({"y", "n"}),
        frozenset({"active", "inactive"}), frozenset({"enabled", "disabled"}),
    }

    for col in df.columns:
        col_str = str(col)
        col_lower = col_str.lower()
        series = df[col]
        null_pct = round(float(series.isna().mean() * 100), 2)
        cardinality = int(series.nunique(dropna=True))
        sample_vals = [str(v) for v in series.dropna().head(6).tolist()]
        entry: dict = {
            "semantic_type": "text",  # default
            "cardinality": cardinality,
            "null_pct": null_pct,
            "sample_values": sample_vals,
        }

        # ── 1. Date detection ────────────────────────────────────────────────
        if pd.api.types.is_datetime64_any_dtype(series):
            entry["semantic_type"] = "date"
            result[col_str] = entry
            continue
        if any(k in col_lower for k in _DATE_NAME_KWS):
            try:
                parsed = _to_datetime_safe(series.dropna().head(20))
                if parsed.notna().mean() >= 0.7:
                    entry["semantic_type"] = "date"
                    result[col_str] = entry
                    continue
            except Exception:
                pass

        # ── 2. Boolean detection ─────────────────────────────────────────────
        if cardinality <= 3:
            unique_lower = {str(v).strip().lower() for v in series.dropna().unique()}
            if unique_lower - {"nan", ""} and frozenset(unique_lower - {"nan", ""}) in _BOOL_VALUES:
                entry["semantic_type"] = "boolean"
                result[col_str] = entry
                continue

        # ── 3. ID detection ──────────────────────────────────────────────────
        _id_name_match = (
            col_lower == "id"
            or col_lower.endswith("_id")
            or col_lower.startswith("id_")
            or "uuid" in col_lower
            or any(col_lower.endswith(k) or col_lower.startswith(k.lstrip("_")) for k in _ID_NAME_KWS)
        )
        if _id_name_match:
            entry["semantic_type"] = "id"
            result[col_str] = entry
            continue
        # High uniqueness ratio for numeric/string columns signals an ID
        if n > 20 and cardinality / n > 0.9 and pd.api.types.is_integer_dtype(series):
            entry["semantic_type"] = "id"
            result[col_str] = entry
            continue

        # ── 4. Numeric-based detection ───────────────────────────────────────
        if pd.api.types.is_numeric_dtype(series):
            # Currency: name keywords
            if any(k in col_lower for k in _CURRENCY_NAME_KWS):
                entry["semantic_type"] = "currency"
                result[col_str] = entry
                continue
            # Percentage: name keywords OR values tightly in [0, 100] with keyword signal
            if any(k in col_lower for k in _PCT_NAME_KWS):
                entry["semantic_type"] = "percentage"
                result[col_str] = entry
                continue
            num_series = pd.to_numeric(series, errors="coerce").dropna()
            if len(num_series) > 0:
                pct_in_0_1 = ((num_series >= 0) & (num_series <= 1)).mean()
                pct_in_0_100 = ((num_series >= 0) & (num_series <= 100)).mean()
                if pct_in_0_1 > 0.95 and "pct" in col_lower:
                    entry["semantic_type"] = "percentage"
                    result[col_str] = entry
                    continue
                if pct_in_0_100 > 0.98 and any(k in col_lower for k in ["pct", "percent", "rate", "ratio"]):
                    entry["semantic_type"] = "percentage"
                    result[col_str] = entry
                    continue
            entry["semantic_type"] = "number"
            result[col_str] = entry
            continue

        # ── 5. String-based detection ────────────────────────────────────────
        # Check if string column looks like currency (leading $ £ € ¥ signs)
        str_sample = series.dropna().head(30).astype(str)
        currency_sign_pct = str_sample.str.match(r"^[\$£€¥₹₩]").mean() if len(str_sample) > 0 else 0
        if currency_sign_pct > 0.5:
            entry["semantic_type"] = "currency"
            result[col_str] = entry
            continue

        # Try parsing as date from string
        try:
            parsed = _to_datetime_safe(series.dropna().head(30))
            if parsed.notna().mean() >= 0.8 and cardinality > 1:
                entry["semantic_type"] = "date"
                result[col_str] = entry
                continue
        except Exception:
            pass

        # Category vs text based on cardinality
        if cardinality <= 50:
            entry["semantic_type"] = "category"
        else:
            entry["semantic_type"] = "text"
        result[col_str] = entry

    return result


def build_profile_summary(df: pd.DataFrame) -> ProfileSummary:
    numeric_columns = [str(c) for c in df.select_dtypes(include=["number"]).columns]
    categorical_columns = [str(c) for c in df.select_dtypes(exclude=["number", "datetime"]).columns]

    suggested_dimensions = categorical_columns[:6]
    suggested_measures = numeric_columns[:6]

    column_types = detect_column_types(df)

    return ProfileSummary(
        total_rows=int(df.shape[0]),
        total_columns=int(df.shape[1]),
        duplicate_rows=int(df.duplicated().sum()),
        missing_cells=int(df.isna().sum().sum()),
        numeric_columns=numeric_columns,
        categorical_columns=categorical_columns,
        suggested_dimensions=suggested_dimensions,
        suggested_measures=suggested_measures,
        column_types=column_types,
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

    try:
        p25 = round(float(col.quantile(0.25)), 2)
        p75 = round(float(col.quantile(0.75)), 2)
        median_val = round(float(col.median()), 2)
    except Exception:
        p25 = p75 = median_val = 0.0

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
        "median_val": median_val,
        "p25": p25,
        "p75": p75,
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
    "indigo":   ["#6366f1", "#8b5cf6", "#a78bfa", "#c4b5fd", "#818cf8", "#4f46e5", "#7c3aed", "#9061f9", "#a855f7", "#d946ef"],
    "blue":     ["#3b82f6", "#60a5fa", "#93c5fd", "#1d4ed8", "#2563eb", "#0ea5e9", "#38bdf8", "#7dd3fc", "#1e40af", "#172554"],
    "emerald":  ["#10b981", "#34d399", "#6ee7b7", "#059669", "#065f46", "#14b8a6", "#2dd4bf", "#5eead4", "#0f766e", "#134e4a"],
    "rose":     ["#f43f5e", "#fb7185", "#fda4af", "#e11d48", "#9f1239", "#f97316", "#fb923c", "#fdba74", "#ea580c", "#7c2d12"],
    "amber":    ["#f59e0b", "#fbbf24", "#fcd34d", "#d97706", "#92400e", "#eab308", "#facc15", "#fde047", "#ca8a04", "#713f12"],
    "slate":    ["#475569", "#64748b", "#94a3b8", "#1e293b", "#334155", "#6b7280", "#9ca3af", "#d1d5db", "#374151", "#111827"],
    "vibrant":  ["#6366f1", "#10b981", "#f59e0b", "#f43f5e", "#3b82f6", "#8b5cf6", "#14b8a6", "#fb923c", "#84cc16", "#ec4899"],
    "ocean":    ["#0ea5e9", "#06b6d4", "#22d3ee", "#0284c7", "#0369a1", "#38bdf8", "#67e8f9", "#0891b2", "#155e75", "#164e63"],
    "sunset":   ["#f97316", "#ef4444", "#ec4899", "#a855f7", "#f59e0b", "#fb923c", "#f43f5e", "#d946ef", "#e11d48", "#9333ea"],
    "mono":     ["#1e293b", "#334155", "#475569", "#64748b", "#94a3b8", "#cbd5e1", "#e2e8f0", "#334155", "#0f172a", "#475569"],
    "neon":     ["#22d3ee", "#a3e635", "#fb923c", "#f472b6", "#c084fc", "#34d399", "#fbbf24", "#f87171", "#60a5fa", "#4ade80"],
    "tropical": ["#06d6a0", "#118ab2", "#ffd166", "#ef476f", "#073b4c", "#00b4d8", "#90e0ef", "#f72585", "#7209b7", "#3a0ca3"],
    "candy":    ["#ff6b9d", "#c44dff", "#48c9b0", "#f7dc6f", "#ff8c42", "#6c5ce7", "#fd79a8", "#00cec9", "#fdcb6e", "#e17055"],
    "aurora":   ["#7400b8", "#6930c3", "#5e60ce", "#5390d9", "#4ea8de", "#48bfe3", "#56cfe1", "#64dfdf", "#72efdd", "#80ffdb"],
}

DEFAULT_PALETTE = PALETTES["vibrant"]

_MULTI_COLORS = [
    "#6366f1", "#10b981", "#f59e0b", "#f43f5e", "#3b82f6", "#8b5cf6",
    "#14b8a6", "#fb923c", "#e11d48", "#2563eb",
]


_TOOLTIP_OPTS = {
    "backgroundColor": "rgba(10,10,20,0.92)",
    "titleColor": "#f8fafc",
    "bodyColor": "#e2e8f0",
    "borderColor": "rgba(99,102,241,0.5)",
    "borderWidth": 1,
    "padding": 14,
    "cornerRadius": 12,
    "displayColors": True,
    "boxWidth": 10,
    "boxHeight": 10,
    "caretSize": 6,
    "titleFont": {"size": 12, "weight": "700"},
    "bodyFont": {"size": 12},
}

_ANIMATION_OPTS = {
    "duration": 900,
    "easing": "easeOutQuart",
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


def _detect_kpi_meta(col_name: str, semantic_type: str = "") -> dict:
    """Detect KPI display metadata (format + icon + unit prefix/suffix) from column name + semantic type.

    Args:
        col_name: Raw column name
        semantic_type: Pre-detected semantic type from detect_column_types()
                       ('currency', 'percentage', 'number', 'date', 'boolean', etc.)

    Returns a dict with:
        format:  'currency' | 'percent' | 'count' | 'number'
        icon:    'money' | 'percent' | 'people' | 'clock' | 'chart'
        prefix:  unit prefix string (e.g. '$', '€', '£', '¥', '')
        suffix:  unit suffix string (e.g. '%', 'x', '')
    """
    lower = str(col_name).lower()

    # Use semantic_type as a strong prior signal if available
    if semantic_type == "percentage":
        return {'format': 'percent', 'icon': 'percent', 'prefix': '', 'suffix': '%'}
    if semantic_type == "currency":
        # Try to detect currency symbol from column name
        if 'eur' in lower or 'euro' in lower:
            return {'format': 'currency', 'icon': 'money', 'prefix': '€', 'suffix': ''}
        if 'gbp' in lower or 'pound' in lower:
            return {'format': 'currency', 'icon': 'money', 'prefix': '£', 'suffix': ''}
        if 'jpy' in lower or 'yen' in lower:
            return {'format': 'currency', 'icon': 'money', 'prefix': '¥', 'suffix': ''}
        if 'inr' in lower or 'rupee' in lower:
            return {'format': 'currency', 'icon': 'money', 'prefix': '₹', 'suffix': ''}
        if 'cny' in lower or 'rmb' in lower or 'yuan' in lower:
            return {'format': 'currency', 'icon': 'money', 'prefix': '¥', 'suffix': ''}
        if 'lkr' in lower:
            return {'format': 'currency', 'icon': 'money', 'prefix': 'LKR ', 'suffix': ''}
        if 'aud' in lower:
            return {'format': 'currency', 'icon': 'money', 'prefix': 'A$', 'suffix': ''}
        if 'cad' in lower:
            return {'format': 'currency', 'icon': 'money', 'prefix': 'C$', 'suffix': ''}
        if 'sgd' in lower:
            return {'format': 'currency', 'icon': 'money', 'prefix': 'S$', 'suffix': ''}
        return {'format': 'currency', 'icon': 'money', 'prefix': '$', 'suffix': ''}

    # Name-based detection (for when semantic_type is not provided)
    if any(k in lower for k in [
        'revenue', 'sales', 'profit', 'cost', 'price', 'amount', 'income',
        'spend', 'budget', 'earning', 'margin', 'value', 'gmv', 'arpu', 'ltv',
        'fee', 'payment', 'invoice', 'receipt', 'cash', 'dollar', 'usd',
        'arr', 'mrr', 'acv', 'tcv', 'aov',
    ]):
        if 'eur' in lower or 'euro' in lower:
            return {'format': 'currency', 'icon': 'money', 'prefix': '€', 'suffix': ''}
        if 'gbp' in lower or 'pound' in lower:
            return {'format': 'currency', 'icon': 'money', 'prefix': '£', 'suffix': ''}
        if 'jpy' in lower or 'yen' in lower:
            return {'format': 'currency', 'icon': 'money', 'prefix': '¥', 'suffix': ''}
        if 'inr' in lower or 'rupee' in lower:
            return {'format': 'currency', 'icon': 'money', 'prefix': '₹', 'suffix': ''}
        if 'lkr' in lower:
            return {'format': 'currency', 'icon': 'money', 'prefix': 'LKR ', 'suffix': ''}
        return {'format': 'currency', 'icon': 'money', 'prefix': '$', 'suffix': ''}
    if any(k in lower for k in [
        'rate', 'ratio', 'pct', 'percent', 'share', 'growth', 'churn',
        'conversion', 'efficiency', 'utilization', 'retention', 'accuracy',
        'discount', 'yield', 'margin_pct', 'markup', 'tax_rate',
    ]):
        return {'format': 'percent', 'icon': 'percent', 'prefix': '', 'suffix': '%'}
    if any(k in lower for k in [
        'count', 'num', 'number', 'qty', 'quantity', 'volume', 'orders',
        'transactions', 'users', 'customers', 'visitors', 'sessions',
        'clicks', 'leads', 'signups', 'views', 'records', 'rows',
        'employees', 'headcount', 'staff', 'members', 'subscribers',
        'tickets', 'cases', 'incidents', 'requests', 'units', 'stores',
        'branches', 'outlets', 'locations', 'offices',
    ]):
        return {'format': 'count', 'icon': 'people', 'prefix': '', 'suffix': ''}
    if any(k in lower for k in [
        'days', 'hours', 'minutes', 'duration', 'time', 'age', 'tenure',
        'latency', 'ttl', 'ttfb', 'response', 'cycle',
    ]):
        return {'format': 'number', 'icon': 'clock', 'prefix': '', 'suffix': ''}
    if any(k in lower for k in ['score', 'rating', 'rank', 'index', 'nps', 'csat']):
        return {'format': 'number', 'icon': 'chart', 'prefix': '', 'suffix': ''}
    if col_name in ('rows', 'records', 'total_rows'):
        return {'format': 'count', 'icon': 'people', 'prefix': '', 'suffix': ''}
    return {'format': 'number', 'icon': 'chart', 'prefix': '', 'suffix': ''}


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


def _bar_config(labels: list, values: list, label: str, palette: str = "vibrant",
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
                "borderRadius": 10,
                "borderSkipped": False,
                "borderWidth": 0,
                "barPercentage": 0.72,
                "categoryPercentage": 0.8,
                "hoverBackgroundColor": [c + "cc" for c in colors],
                "hoverBorderWidth": 0,
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


def _line_config(labels: list, values: list, label: str, palette: str = "aurora",
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
                "backgroundColor": border + "22",
                "tension": 0.42,
                "fill": False,
                "pointRadius": 4,
                "pointHoverRadius": 8,
                "pointBackgroundColor": border,
                "pointBorderColor": "#ffffff",
                "pointBorderWidth": 2.5,
                "borderWidth": 3,
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


def _area_config(labels: list, values: list, label: str, palette: str = "tropical",
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
                        {"offset": 0, "color": border + "66"},
                        {"offset": 1, "color": border + "08"},
                    ],
                },
                "tension": 0.42,
                "fill": True,
                "pointRadius": 4,
                "pointHoverRadius": 8,
                "pointBackgroundColor": border,
                "pointBorderColor": "#ffffff",
                "pointBorderWidth": 2.5,
                "borderWidth": 3,
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


def _pie_config(labels: list, values: list, palette: str = "vibrant") -> dict:
    colors = _resolve_palette(palette, len(labels))
    return {
        "type": "pie",
        "data": {
            "labels": labels,
            "datasets": [{
                "data": values,
                "backgroundColor": colors,
                "hoverOffset": 16,
                "borderWidth": 2,
                "borderColor": "#ffffff",
                "hoverBorderColor": "#ffffff",
                "hoverBorderWidth": 3,
                "offset": 4,
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


def _doughnut_config(labels: list, values: list, palette: str = "candy") -> dict:
    colors = _resolve_palette(palette, len(labels))
    return {
        "type": "doughnut",
        "data": {
            "labels": labels,
            "datasets": [{
                "data": values,
                "backgroundColor": colors,
                "hoverOffset": 16,
                "borderWidth": 2,
                "borderColor": "#ffffff",
                "hoverBorderColor": "#ffffff",
                "hoverBorderWidth": 3,
                "offset": 4,
            }],
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "cutout": "72%",
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


def _hbar_config(labels: list, values: list, label: str, palette: str = "tropical",
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
                "borderWidth": 0,
                "barPercentage": 0.75,
                "categoryPercentage": 0.85,
                "hoverBackgroundColor": [c + "cc" for c in colors],
                "hoverBorderWidth": 0,
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


def _radar_config(labels: list, values: list, label: str, palette: str = "candy") -> dict:
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
                "backgroundColor": border + "33",
                "pointRadius": 5,
                "pointHoverRadius": 7,
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
            "scales": {
                "r": {
                    "ticks": {"color": "#94a3b8", "backdropColor": "transparent", "font": {"size": 10}},
                    "grid": {"color": "rgba(148,163,184,0.15)"},
                    "angleLines": {"color": "rgba(148,163,184,0.2)"},
                    "pointLabels": {"color": "#64748b", "font": {"size": 11, "weight": "500"}},
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
    """Return (client, model) tuple for the configured AI provider, or (None, None).

    Priority: DeepSeek (if DEEPSEEK_API_KEY set) → Gemini (if GEMINI_API_KEY set).
    Gemini is accessed via its OpenAI-compatible endpoint so no extra SDK is needed.
    """
    import importlib.util
    from django.conf import settings

    if importlib.util.find_spec("openai") is None:
        return None, None
    openai_module = __import__("openai")

    # ── DeepSeek (primary) ────────────────────────────────────────────────────
    deepseek_key = getattr(settings, "DEEPSEEK_API_KEY", "")
    if deepseek_key:
        model = getattr(settings, "DEEPSEEK_MODEL", "deepseek-chat")
        max_retries = int(getattr(settings, "DEEPSEEK_MAX_RETRIES", 0))
        client = openai_module.OpenAI(
            api_key=deepseek_key,
            base_url="https://api.deepseek.com",
            max_retries=max_retries,
        )
        return client, model

    # ── Gemini (fallback via OpenAI-compatible API) ───────────────────────────
    gemini_key = getattr(settings, "GEMINI_API_KEY", "")
    if gemini_key:
        model = getattr(settings, "GEMINI_MODEL", "gemini-2.0-flash")
        client = openai_module.OpenAI(
            api_key=gemini_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
        return client, model

    return None, None


def ai_detect_column_roles(df: pd.DataFrame, profile: "ProfileSummary") -> dict:
    """AI-powered column role detection: classifies each column as measure/dimension/date/id.

    Returns dict: {column_name: {role, data_type, agg, label, cardinality}}

    Roles:
    - 'measure': quantitative, aggregatable (revenue, amount, count, score, rate, price)
    - 'dimension': categorical grouping variables (region, category, name, type, status)
    - 'date': temporal columns (date, month, year, quarter, period, timestamp)
    - 'id': identifier/key columns to skip (id, uuid, code, ref, key, index)

    data_type (fine-grained):
    - 'currency': monetary values ($, £, €, revenue, cost, price, sales)
    - 'percentage': rates, ratios, pct, utilization (0-100 or 0-1 range)
    - 'number': generic integer/float metric
    - 'date': temporal / time-series
    - 'boolean': binary flag (yes/no, true/false, active/inactive)
    - 'category': low-to-medium cardinality string grouping variable
    - 'text': free-form text / high cardinality string
    - 'id': surrogate key / identifier

    agg: best aggregation ('sum', 'avg', 'count', 'max', 'min', 'group', 'none')
    cardinality: for dimensions ('low'<10, 'medium'=10-50, 'high'>50), None for others
    """
    import json as _json
    import re as _re

    client, model = _get_ai_client()

    # Incorporate heuristic column types for richer context
    heuristic_types = profile.column_types or detect_column_types(df)

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
            col_type_info = heuristic_types.get(str(col), {})
            sem_type = col_type_info.get("semantic_type", "number")
            if sem_type == "currency":
                agg = "sum"
            elif sem_type == "percentage":
                agg = "avg"
            else:
                agg = "sum"
            roles[str(col)] = {
                "role": "measure", "data_type": sem_type, "agg": agg,
                "label": _humanize_col(col), "cardinality": None,
            }
        for col in profile.categorical_columns:
            try:
                card = int(df[col].nunique(dropna=True))
                tier = "low" if card < 10 else "medium" if card < 50 else "high"
            except Exception:
                tier = "medium"
            col_type_info = heuristic_types.get(str(col), {})
            sem_type = col_type_info.get("semantic_type", "category")
            roles[str(col)] = {
                "role": "dimension", "data_type": sem_type, "agg": "group",
                "label": _humanize_col(col), "cardinality": tier,
            }
        date_keywords = ["date", "month", "year", "period", "quarter", "time", "timestamp"]
        for col in df.columns:
            col_lower = str(col).lower()
            if any(k in col_lower for k in date_keywords):
                roles[str(col)] = {
                    "role": "date", "data_type": "date", "agg": "none",
                    "label": _humanize_col(col), "cardinality": None,
                }
        return roles

    if client is None:
        return _heuristic_roles()

    # Include heuristic types in payload to give AI richer context
    col_semantic_types = {str(c): v.get("semantic_type", "") for c, v in heuristic_types.items()}

    payload = {
        "columns": [str(c) for c in df.columns[:50]],
        "sample_values": col_samples,
        "numeric_cols": [str(c) for c in profile.numeric_columns[:20]],
        "categorical_cols": [str(c) for c in profile.categorical_columns[:20]],
        "column_stats": col_stats,
        "heuristic_semantic_types": col_semantic_types,
        "total_rows": int(profile.total_rows),
    }

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a senior BI data architect specializing in dimensional modeling and column classification "
                        "for multinational enterprise analytics platforms. "
                        "Classify EVERY column into exactly one role AND one data_type.\n\n"
                        "ROLES:\n"
                        "1. 'measure': Quantitative, aggregatable numeric values\n"
                        "   Examples: revenue, sales, amount, quantity, score, rate, price, cost, profit, "
                        "margin, duration, weight, distance, age, salary, count, balance, volume\n"
                        "   IMPORTANT: numeric IDs that represent real quantities (e.g. order_value, "
                        "employee_count) are measures, not ids.\n\n"
                        "2. 'dimension': Categorical grouping/segmentation variables\n"
                        "   Examples: region, category, name, type, status, segment, product, department, "
                        "country, channel, brand, gender, tier, level, class, group\n"
                        "   IMPORTANT: columns with low cardinality numeric codes (e.g. rating 1-5, "
                        "grade A-F) are dimensions if they segment data meaningfully.\n\n"
                        "3. 'date': Any temporal column — use sample_values to confirm\n"
                        "   Examples: date, month, year, quarter, period, timestamp, created_at, "
                        "updated_at, order_date, event_date, hire_date, expiry_date\n"
                        "   IMPORTANT: integer years (e.g. 2020, 2021) are dates if column name "
                        "contains 'year'/'period'; otherwise treat as dimension.\n\n"
                        "4. 'id': Pure identifier/surrogate key columns with no analytical value\n"
                        "   Examples: id, uuid, row_id, record_id, pk, fk, index, serial_number\n"
                        "   IMPORTANT: only classify as 'id' if the column is clearly a surrogate key "
                        "with no business meaning.\n\n"
                        "DATA TYPES (fine-grained):\n"
                        "- 'currency': monetary values — revenue, cost, price, sales, profit, salary, budget, "
                        "income, spend, GMV, LTV, ARR, fee, payment, invoice ($, £, €, ¥, ₹ etc.)\n"
                        "- 'percentage': rate/ratio — churn rate, conversion %, growth %, utilization, "
                        "efficiency, retention, margin %, discount, yield, accuracy\n"
                        "- 'number': generic integer/float — count, quantity, units, score, volume, "
                        "weight, age, distance, duration, headcount\n"
                        "- 'date': temporal data — dates, timestamps, periods, years, months, quarters\n"
                        "- 'boolean': binary flag — active/inactive, yes/no, true/false, approved/pending\n"
                        "- 'category': low-to-medium cardinality string — region, status, type, "
                        "department, brand, tier, segment (≤50 unique values)\n"
                        "- 'text': high cardinality or free text — names, descriptions, notes, "
                        "addresses, URLs, emails (>50 unique values)\n"
                        "- 'id': identifier/key — no analytical value\n\n"
                        "Use 'heuristic_semantic_types' from the payload as a starting point but override "
                        "when you have stronger evidence from sample_values or column name.\n\n"
                        "FOR EACH COLUMN specify:\n"
                        "- role: measure | dimension | date | id\n"
                        "- data_type: currency | percentage | number | date | boolean | category | text | id\n"
                        "- agg: sum (currency/count), avg (rates/ratios/scores), count (occurrences), "
                        "nunique (distinct entity counts), max/min (extremes), group (dimensions), none (id/date)\n"
                        "- label: human-friendly business label in Title Case\n"
                        "  ('total_revenue_usd' → 'Total Revenue (USD)', 'num_empl' → 'Employees', "
                        "'avg_score' → 'Avg Score', 'cust_id' → 'Customer ID')\n"
                        "- cardinality: for dimensions only — 'low' (<10), 'medium' (10-50), 'high' (>50); "
                        "null for measures, dates, ids\n\n"
                        "RETURN ONLY valid JSON (no markdown, no extra text):\n"
                        "{\"roles\": {\"column_name\": {\"role\": \"measure|dimension|date|id\", "
                        "\"data_type\": \"currency|percentage|number|date|boolean|category|text|id\", "
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
                    roles[str(col)] = fallback.get(str(col), {
                        "role": "dimension", "data_type": "category",
                        "agg": "group", "label": _humanize_col(col), "cardinality": "medium",
                    })
                # Backfill data_type if missing from AI response
                if "data_type" not in roles.get(str(col), {}):
                    ht = heuristic_types.get(str(col), {})
                    roles[str(col)]["data_type"] = ht.get("semantic_type", "number")
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

    # Build rich statistics context including quartiles and distribution shape
    numeric_stats: dict = {}
    for col in profile.numeric_columns[:12]:
        try:
            s = df[col].dropna()
            mean_v = float(s.mean())
            numeric_stats[str(col)] = {
                "sum": round(float(s.sum()), 2),
                "mean": round(mean_v, 2),
                "median": round(float(s.median()), 2),
                "std": round(float(s.std()), 2),
                "min": round(float(s.min()), 2),
                "max": round(float(s.max()), 2),
                "p25": round(float(s.quantile(0.25)), 2),
                "p75": round(float(s.quantile(0.75)), 2),
                "p90": round(float(s.quantile(0.90)), 2),
                "count_non_null": int(s.count()),
                "above_mean_pct": round(float((s > mean_v).mean() * 100), 1),
                "skewness": round(float(s.skew()), 2),
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
                        "The payload now includes quartile stats (p25, p75, p90), skewness, and above_mean_pct. "
                        "USE THESE to produce distribution-aware insights, not just total/average.\n\n"
                        "REQUIREMENTS:\n"
                        "executive_summary: 3 sentences synthesizing the overall data story. "
                        "Sentence 1: dataset scope (rows, columns, domain). "
                        "Sentence 2: primary metric total + most important categorical breakdown (cite exact numbers). "
                        "Sentence 3: key risk/opportunity or strategic implication.\n\n"
                        "key_findings: Exactly 5 specific, numbered insights. "
                        "EVERY finding MUST cite at least TWO specific numbers (e.g. total AND p75, mean AND std). "
                        "Include distribution insights (skewness, quartile spread, above-mean percentage). "
                        "Order by business impact (most impactful first). "
                        "Format: 'Finding [N]: [Primary number]. [Distribution context]. [Business implication].'\n\n"
                        "strategic_recs: 3 action-oriented recommendations. "
                        "Each MUST specify WHO does WHAT, targeting WHICH metric, and WHY (cite the specific gap or opportunity). "
                        "Example: 'Revenue team should focus on the top-quartile customer segment ($3,200+ orders) "
                        "which represents 25% of customers but 58% of revenue — a prime expansion target.'\n\n"
                        "data_health: 1-2 sentences on data completeness (missing %, duplicates) "
                        "and any data quality concern that could affect analytical conclusions.\n\n"
                        "analyst_note: 1 sentence expert insight on what the distribution shape (skewness, quartile spread) "
                        "or notable correlation reveals about the business's structure or maturity.\n\n"
                        "STYLE: Board-level briefing tone. Authoritative, data-dense, zero filler words. "
                        "Every sentence must contain a number. No vague phrases.\n\n"
                        "Return ONLY valid JSON (no markdown, no extra text):\n"
                        "{\"executive_summary\":\"3 sentences with specific numbers\","
                        "\"key_findings\":[\"Finding 1: primary number + distribution context\","
                        "\"Finding 2: ...\",\"Finding 3: ...\",\"Finding 4: ...\",\"Finding 5: ...\"],"
                        "\"strategic_recs\":[\"Rec 1: WHO does WHAT targeting WHICH metric\","
                        "\"Rec 2: ...\",\"Rec 3: ...\"],"
                        "\"data_health\":\"missing % + quality implication\","
                        "\"analyst_note\":\"distribution/correlation expert insight\"}"
                    ),
                },
                {"role": "user", "content": _json.dumps(payload)},
            ],
            temperature=0.2,
            stream=True,
            timeout=45,
        )
        # Stream chunks to avoid read timeout on long narrative responses
        content_parts: list[str] = []
        for chunk in response:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                content_parts.append(delta)
        content = "".join(content_parts).strip()
        content = _re.sub(r"```(?:json)?\s*", "", content).strip().rstrip("```").strip()
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
        # Include sample values per categorical column for better AI context
        sample_values: dict = {}
        for col in profile.categorical_columns[:30]:
            try:
                sample_values[str(col)] = list(df[col].value_counts().head(5).index.astype(str))
            except Exception:
                pass

        payload = {
            "columns": list(df.columns[:60]),
            "dtypes": {str(c): str(df[c].dtype) for c in df.columns[:60]},
            "numeric_columns": profile.numeric_columns[:30],
            "categorical_columns": profile.categorical_columns[:30],
            "cardinality": {
                str(c): int(df[c].nunique(dropna=True))
                for c in profile.categorical_columns[:30]
            },
            "sample_values": sample_values,
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
                            "- dropdown: categorical columns with 2-12 unique values (fast single-select lookup)\n"
                            "- multiselect: categorical columns with 6-50 unique values (multi-value comparison)\n"
                            "- range: numeric columns for continuous value narrowing (revenue, age, quantity, score)\n"
                            "- AVOID: pure ID columns (uuid, primary keys, row numbers, record identifiers)\n"
                            "- AVOID: high cardinality text fields (free-text notes, descriptions, names with >100 values)\n"
                            "- PRIORITIZE: time periods, geography (region/country/state), category/segment, "
                            "status/type (active/inactive, pending/approved), tier/level/grade\n"
                            "- Use sample_values to understand what each categorical column contains — "
                            "this helps distinguish business dimensions from technical fields\n"
                            "- Name labels as business users would expect: "
                            "'region_code' → 'Region', 'product_category_name' → 'Product Category', "
                            "'order_status' → 'Order Status'\n\n"
                            "OUTPUT FORMAT — return ONLY a valid JSON array (no markdown, no extra text):\n"
                            '[\n'
                            '  {"column": "region", "filter_type": "dropdown", "label": "Region", '
                            '"reason": "5 regions enable fast executive segment drill-down"},\n'
                            '  {"column": "product_category", "filter_type": "multiselect", "label": "Product Category", '
                            '"reason": "12 categories enable cross-category performance comparison"},\n'
                            '  {"column": "revenue", "filter_type": "range", "label": "Revenue Range", '
                            '"reason": "Range filter isolates high-value customers from low-value ones"}\n'
                            "]"
                        ),
                    },
                    {"role": "user", "content": _json.dumps(payload)},
                ],
                temperature=0.1,
                stream=False,
                timeout=15,
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
                        "You are a senior BI analyst writing authoritative chart commentary "
                        "for an executive-level professional dashboard report.\n\n"
                        "TASK: Write 3 punchy, numerically specific insights for the chart. "
                        "Use ONLY the pre-computed statistics provided — cite precise values, labels, and percentages.\n\n"
                        "RULES:\n"
                        "- Plain text ONLY — no markdown, no bullet points, no headers, no hyphens.\n"
                        "- EVERY sentence must contain at least one specific number from the statistics.\n"
                        "- NEVER use vague phrases: 'shows a trend', 'indicates patterns', 'data reveals', "
                        "'interesting to note', 'worth mentioning', 'it appears'.\n"
                        "- bar/hbar: name the #1 performer with exact value + % of total. "
                        "State how many of {data_points} categories are above the average. "
                        "Name the bottom performer and the spread_ratio.\n"
                        "- line/area: state trend direction + exact % change (start_to_end_pct_change). "
                        "Name the peak period and its value. If coefficient_of_variation_pct > 30, call out volatility.\n"
                        "- pie/doughnut/polararea: state dominant segment's exact % (top_pct_of_total). "
                        "State top-2 combined % (top2_combined_pct). Name smallest segment.\n"
                        "- scatter/bubble: state correlation direction. If spread_ratio > 5, "
                        "call out the extreme outlier range.\n"
                        "- radar: identify highest and lowest dimension with exact values. "
                        "Cite the range spread (max - min).\n"
                        "- End with ONE forward-looking sentence citing which label/segment to prioritize or investigate.\n"
                        "- Maximum 120 words total. Be authoritative, specific, and actionable."
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


def deduplicate_chart_specs(specs: list[dict]) -> list[dict]:
    """Remove duplicate chart widget specs to ensure every chart shows unique data.

    A duplicate is defined as two non-structural widgets (not kpi/heading/text_canvas/table)
    sharing the same: chart_type + primary dimension + primary measure.

    Strategy:
    1. Keep all structural widgets (headings, narrative, kpi, table) — they are always preserved.
    2. For chart widgets: track (chart_type, dimension, measure) tuples.
       If a duplicate key is found, skip the later occurrence.
    3. Additionally, limit each chart_type to at most 2 occurrences overall
       (e.g. max 2 bar charts, max 2 line charts, etc.) to force visual diversity.
    """
    _STRUCTURAL = {"kpi", "heading", "text_canvas", "table"}
    _MAX_PER_TYPE = {
        "bar": 2, "hbar": 2, "line": 2, "area": 2,
        "pie": 1, "doughnut": 1, "scatter": 2, "radar": 1,
        "bubble": 1, "polararea": 1, "mixed": 1, "funnel": 1,
        "gauge": 2, "waterfall": 1,
    }

    seen_keys: set[tuple] = set()
    type_counts: dict[str, int] = {}
    result: list[dict] = []

    for spec in specs:
        chart_type = str(spec.get("chart_type") or spec.get("widget_type") or "").lower()

        # Always keep structural widgets
        if chart_type in _STRUCTURAL:
            result.append(spec)
            continue

        # Build uniqueness key from chart_type + dimension + first measure
        dimension = str(spec.get("dimension") or "").strip()
        measures = spec.get("measures") or []
        first_measure = str(measures[0]).strip() if measures else ""
        unique_key = (chart_type, dimension, first_measure)

        # Check for exact duplicate
        if unique_key in seen_keys and dimension:  # allow charts without dimension (scatter, gauge)
            logger.debug("Dedup: skipping duplicate chart '%s' (%s)", spec.get("title"), unique_key)
            continue

        # Enforce per-type limits to maintain visual diversity
        current_count = type_counts.get(chart_type, 0)
        max_allowed = _MAX_PER_TYPE.get(chart_type, 3)
        if current_count >= max_allowed:
            logger.debug("Dedup: skipping '%s' — %s limit %d reached", spec.get("title"), chart_type, max_allowed)
            continue

        seen_keys.add(unique_key)
        type_counts[chart_type] = current_count + 1
        result.append(spec)

    return result


def ai_generate_dashboard_specs(
    df: pd.DataFrame,
    profile: "ProfileSummary",
    dataset_name: str = "",
    plan: str = "free",
    column_roles: dict | None = None,
) -> list[dict] | None:
    """Ask AI to design a schema-agnostic, comprehensive dashboard plan and normalize it to widget specs.

    Returns a list of widget specs or None if AI is unavailable.
    Each spec includes an 'ai_insight' field with a data-driven analytical insight.
    Narrative and section headings are injected at appropriate positions.

    Args:
        plan: User subscription plan ('free', 'pro', 'enterprise'). Controls available chart types.
              Free: basic charts only. Pro/Enterprise: all advanced chart types.
        column_roles: Pre-computed column role info with data_type for smarter chart selection.
    """
    import json as _json
    import re as _re
    from django.conf import settings

    client, model = _get_ai_client()
    if client is None:
        return None
    specs_timeout = int(getattr(settings, "DEEPSEEK_SPECS_TIMEOUT", 60))
    connect_timeout = int(getattr(settings, "DEEPSEEK_CONNECT_TIMEOUT", 10))

    # Determine allowed chart types based on user plan
    _plan_lower = str(plan).lower()
    _is_pro = _plan_lower in ("pro", "enterprise")
    _FREE_CHART_TYPES = ["kpi", "bar", "line", "area", "pie", "doughnut", "hbar", "scatter", "radar", "table"]
    _PRO_CHART_TYPES_LIST = ["bubble", "polararea", "mixed", "funnel", "gauge", "waterfall"]
    allowed_chart_types = _FREE_CHART_TYPES + (_PRO_CHART_TYPES_LIST if _is_pro else [])

    date_cols = [c for c in df.columns if any(k in str(c).lower() for k in ["date", "month", "year", "period", "quarter"])]
    # Also detect date columns by semantic type
    col_types = profile.column_types or {}
    for col, type_info in col_types.items():
        if type_info.get("semantic_type") == "date" and col not in date_cols:
            date_cols.append(col)

    # Compute rich statistical context for AI (including quartiles and distribution shape)
    sample_stats: dict = {}
    for col in profile.numeric_columns[:12]:
        try:
            s = df[col].dropna()
            total_val = round(float(s.sum()), 2)
            mean_val = round(float(s.mean()), 2)
            sample_stats[str(col)] = {
                "sum": total_val,
                "mean": mean_val,
                "median": round(float(s.median()), 2),
                "std": round(float(s.std()), 2),
                "min": round(float(s.min()), 2),
                "max": round(float(s.max()), 2),
                "p25": round(float(s.quantile(0.25)), 2),
                "p75": round(float(s.quantile(0.75)), 2),
                "p90": round(float(s.quantile(0.90)), 2),
                "non_null_count": int(s.count()),
                "null_count": int(df[col].isna().sum()),
                "skewness": round(float(s.skew()), 2),
                "above_mean_pct": round(float((s > mean_val).mean() * 100), 1),
                "human_label": _humanize_col(col),
            }
        except Exception:
            pass

    # Compute pairwise correlations between top numeric columns
    correlation_matrix: dict = {}
    try:
        num_cols = [c for c in profile.numeric_columns[:8] if pd.api.types.is_numeric_dtype(df[c])]
        if len(num_cols) >= 2:
            corr = df[num_cols].corr(numeric_only=True)
            for c1 in num_cols:
                for c2 in num_cols:
                    if c1 < c2:
                        val = round(float(corr.loc[c1, c2]), 2) if not pd.isna(corr.loc[c1, c2]) else 0.0
                        if abs(val) >= 0.4:  # only include notable correlations
                            correlation_matrix[f"{c1} vs {c2}"] = val
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

    # Build column semantic type map for AI context
    col_semantic_types: dict = {}
    col_types_local = profile.column_types or {}
    for col in df.columns[:60]:
        col_str = str(col)
        if col_str in col_types_local:
            col_semantic_types[col_str] = col_types_local[col_str].get("semantic_type", "")
        elif column_roles and col_str in column_roles:
            col_semantic_types[col_str] = column_roles[col_str].get("data_type", "")

    # Build currency/percentage column lists for AI to prefer correct aggregations
    currency_cols = [c for c, t in col_semantic_types.items() if t == "currency"]
    percentage_cols = [c for c, t in col_semantic_types.items() if t == "percentage"]
    boolean_cols = [c for c, t in col_semantic_types.items() if t == "boolean"]
    text_cols = [c for c, t in col_semantic_types.items() if t == "text"]

    payload = {
        "dataset_name": str(dataset_name or "").strip(),
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
        "notable_correlations": correlation_matrix,
        # Column semantic types for smarter chart and aggregation selection
        "column_semantic_types": col_semantic_types,
        "currency_columns": currency_cols,
        "percentage_columns": percentage_cols,
        "boolean_columns": boolean_cols,
        "text_columns": text_cols,
        # Plan-based chart type gating
        "user_plan": _plan_lower,
        "allowed_chart_types": allowed_chart_types,
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
                "ai_insight": full_insight[:600],
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
                "ai_insight": insight[:600],
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
                "ai_insight": insight[:600],
            })

        return specs

    # Build plan-specific instruction for chart types
    plan_chart_instruction = (
        f"User plan: {_plan_lower.upper()}. "
        f"ONLY use chart types from this list: {allowed_chart_types}. "
        + (
            "Advanced charts available (bubble, polararea, mixed, funnel, gauge, waterfall) — "
            "use them STRATEGICALLY where they add unique analytical value. "
            "funnel → stage/conversion data, gauge → single KPI vs target, "
            "waterfall → period-over-period variance, bubble → 3-variable relationship, "
            "polararea → category comparison, mixed → bar+line dual-axis overlay."
            if _is_pro else
            "Free plan: use only bar, line, area, pie, doughnut, hbar, scatter, radar, table, kpi. "
            "NEVER suggest bubble, polararea, mixed, funnel, gauge, or waterfall."
        )
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a world-class Senior BI Dashboard Architect and Data Scientist. "
                        "Your dashboards are trusted by Fortune 500 CEOs, CFOs, and boards of directors at "
                        "multinational companies across Finance, Retail, Healthcare, Manufacturing, and Technology. "
                        "Every widget must earn its place — insight-dense, narratively coherent, visually purposeful.\n\n"

                        "Design a COMPREHENSIVE, schema-agnostic enterprise dashboard for the dataset below.\n"
                        "Use 'dataset_name' and column names to infer the business domain "
                        "(Sales, HR, Finance, Supply Chain, Marketing, Operations, E-commerce, Logistics, etc.).\n"
                        "Mode: executive | analytical | operational — adjust insight depth accordingly.\n"
                        "'notable_correlations' → suggest scatter charts for each correlated pair (r≥0.4).\n"
                        "Stats include sum/mean/median/p25/p75/p90/skewness/above_mean_pct — "
                        "CITE THESE EXACT NUMBERS for richer, data-dense insights.\n\n"

                        "═══ PLAN & CHART TYPE RULES ═══\n"
                        f"{plan_chart_instruction}\n\n"

                        "═══ COLUMN TYPE AWARENESS ═══\n"
                        "The payload includes 'column_semantic_types' — use these to make smarter decisions:\n"
                        "- currency columns → use agg=sum, prefix=$, format large values as $1.2M\n"
                        "- percentage columns → use agg=avg, show as %, use for conversion/efficiency KPIs\n"
                        "- date columns → ALWAYS create at least 1 line chart and 1 area chart for trends\n"
                        "- boolean columns → ideal for doughnut/pie (yes/no split) or bar (by status)\n"
                        "- category columns (low cardinality ≤10) → bar or doughnut/pie\n"
                        "- category columns (medium 10-50) → hbar (ranked top-N list)\n"
                        "- text/id columns → NEVER use as measure or dimension in charts\n\n"

                        "═══ CRITICAL: COLUMN NAME RULES ═══\n"
                        "ALL 'measure', 'x', 'y', 'x_measure', 'y_measure', and 'columns' values MUST be "
                        "copied VERBATIM from the payload 'columns' list. Zero tolerance for invented names.\n"
                        "Verify each column name character-by-character against the list before writing it.\n"
                        "NEVER use text or id columns as chart dimensions or measures.\n\n"

                        "═══ UNIQUENESS RULE (CRITICAL) ═══\n"
                        "EVERY chart MUST be unique — no two charts can use the same chart_type + x + y combination.\n"
                        "Each chart visualizes a DIFFERENT business question or data relationship.\n"
                        "Maximize insight coverage: financial performance, operational efficiency, "
                        "geographic/segment breakdown, trend analysis, correlation, distribution.\n\n"

                        "═══ SECTION TITLE RULES ═══\n"
                        "- 'kpi_section_title': Domain-specific heading. "
                        "GOOD: 'Sales Revenue KPIs', 'Workforce Productivity Metrics', 'Financial Health Indicators'. "
                        "BAD: 'Key Metrics', 'KPI Overview', 'Dashboard Summary'.\n"
                        "- 'chart_section_title': Action-oriented header. "
                        "GOOD: 'Revenue Performance Deep-Dive', 'Customer Acquisition Trends', 'Product Mix Analysis'. "
                        "BAD: 'Charts Section', 'Analytics', 'Performance'.\n"
                        "- 'table_section_title': Specific record type. "
                        "GOOD: 'Top Transactions by Value', 'Employee Detail Records', 'Order History'. "
                        "BAD: 'Data Table', 'Records', 'Details'.\n\n"

                        "═══ KPI RULES ═══\n"
                        "Generate 5-7 DISTINCT KPIs — each serving a unique analytical purpose:\n"
                        "  1. Total volume KPI: primary currency/financial metric (agg=sum) — e.g. 'Total Revenue'\n"
                        "  2. Average benchmark KPI: per-unit or rate metric (agg=avg) — e.g. 'Avg Order Value'\n"
                        "  3. Entity count KPI: unique entities for scope (agg=nunique) — e.g. 'Active Customers'\n"
                        "  4. Peak performance KPI: maximum value (agg=max) — e.g. 'Peak Single Transaction'\n"
                        "  5. Secondary volume KPI: another numeric column (agg=sum or count)\n"
                        "  6. Percentage/rate KPI: use a percentage column (agg=avg) if available\n"
                        "  7. Growth or health KPI: margin, efficiency, or data quality metric\n\n"
                        "KPI naming rules (non-negotiable):\n"
                        "  GOOD: 'Total Revenue', 'Avg Order Value', 'Active Customers', 'Peak Transaction'\n"
                        "  BAD: 'sales_amount', 'col_revenue_usd', 'KPI 1', 'Value', 'Metric'\n"
                        "For currency KPIs: format large sums as '$1.2M' or '€450K' in the insight.\n"
                        "For percentage KPIs: show as '18.3%' with avg/median context.\n\n"
                        "'measure' MUST be an exact column name from payload 'columns'.\n"
                        "'agg': sum (currency/volume), avg (rates/percentages/scores), "
                        "count (records), nunique (distinct entities), max/min (extremes)\n"
                        "'insight': 2 sentences with specific numbers from sample_stats. "
                        "Sentence 1: exact total/avg + record count. "
                        "Sentence 2: distribution (p75 vs median, skewness, % above mean).\n"
                        "'change': benchmark string or null.\n\n"

                        "═══ CHART SELECTION RULES ═══\n"
                        "Generate 7-12 charts. ALL must be UNIQUE (different chart_type OR different x+y). "
                        "Cover ALL these analytical patterns:\n"
                        "  • Date column present → MUST include: line (primary metric over time) + "
                        "area (secondary metric or cumulative), both size=lg\n"
                        "  • notable_correlations (r≥0.4) → scatter chart for each pair\n"
                        "  • Category cardinality 2-10 + currency/number → vertical bar chart\n"
                        "  • Category cardinality >10 + numeric → horizontal bar (hbar) with top-12\n"
                        "  • Part-to-whole breakdown (cardinality ≤8) → doughnut chart\n"
                        "  • Secondary part-to-whole (DIFFERENT category) → pie chart\n"
                        "  • Multi-numeric columns → radar (performance across dimensions)\n"
                        "  • Pro: stage/funnel data → funnel chart\n"
                        "  • Pro: 3-variable relationship → bubble chart\n"
                        "  • Pro: dual-axis (volume + rate) → mixed bar+line\n"
                        "  • Pro: single metric vs target → gauge\n"
                        "  • Financial P&L or period variance → waterfall (Pro)\n\n"
                        "Chart titles MUST answer a business question (NOT column names):\n"
                        "  GOOD: 'Monthly Revenue Growth Trend', 'Revenue by Product Category', "
                        "'Top 10 Customers by Spend', 'Sales vs Target Comparison'\n"
                        "  BAD: 'sales_amount trend', 'bar of qty', 'Column2 vs Column3'\n\n"
                        "Chart insights (2 sentences each, cite exact numbers from sample_stats):\n"
                        "  GOOD: 'Electronics drives 38% of revenue at $1.6M, 2.8x the next category. "
                        "The bottom 4 categories combined account for only 12% — consolidation opportunity.'\n\n"

                        "═══ SIZE RULES ═══\n"
                        "kpi=sm, line/area/waterfall/mixed=lg, bar/hbar/funnel=md, "
                        "pie/doughnut/radar/scatter/bubble/polararea/gauge=md, table=lg\n\n"

                        "═══ PALETTE RULES ═══\n"
                        "emerald=revenue/growth/profit (positive), rose=loss/churn/risk/cost (negative), "
                        "ocean=time-series/trends, vibrant=multi-category comparisons, "
                        "amber=ranking/distribution, sunset=scatter/correlations/bubble, "
                        "indigo=neutral/KPIs/headings, blue=secondary trends, "
                        "tropical=hbar rankings, candy=doughnut/pie\n\n"

                        "Return ONLY valid JSON — no markdown fences, no comments, no trailing commas:\n"
                        "{"
                        "\"narrative\":\"3-sentence executive summary: scope (rows+domain) + "
                        "primary metric (cite exact sum/total) + strategic implication or risk\","
                        "\"kpi_section_title\":\"Domain-specific KPI heading\","
                        "\"chart_section_title\":\"Action-oriented chart section header\","
                        "\"table_section_title\":\"Specific record-type table heading\","
                        "\"kpis\":[{"
                        "\"name\":\"Business-friendly 2-5 word KPI title\","
                        "\"measure\":\"EXACT_col_from_columns_list\","
                        "\"agg\":\"sum|avg|count|nunique|max|min\","
                        "\"change\":\"benchmark string or null\","
                        "\"insight\":\"2 sentences: exact value+count, then distribution context\""
                        "}],"
                        "\"charts\":[{"
                        "\"type\":\"bar|line|area|hbar|pie|doughnut|scatter|radar|bubble|polararea|mixed|funnel|gauge|waterfall\","
                        "\"title\":\"Business question or finding as chart title\","
                        "\"x\":\"EXACT_col_from_columns_list\","
                        "\"y\":[\"EXACT_col_from_columns_list\"],"
                        "\"x_measure\":\"\","
                        "\"y_measure\":\"\","
                        "\"size\":\"sm|md|lg\","
                        "\"palette\":\"emerald|rose|ocean|vibrant|amber|sunset|indigo|blue|tropical|candy\","
                        "\"insight\":\"2 sentences with exact numbers and strategic action\""
                        "}],"
                        "\"tables\":[{"
                        "\"title\":\"Descriptive record-type table name\","
                        "\"columns\":[\"EXACT_col\",\"EXACT_col\",\"EXACT_col\"],"
                        "\"insight\":\"1 data-driven sentence with specific numbers\""
                        "}],"
                        "\"insights\":[\"Global finding 1: specific number + implication\","
                        "\"Finding 2: quartile insight + action\","
                        "\"Finding 3: correlation or anomaly\"]"
                        "}"
                    ),
                },
                {"role": "user", "content": _json.dumps(payload)},
            ],
            temperature=0.15,
            stream=True,
            timeout=__import__("httpx").Timeout(
                connect=float(connect_timeout),
                read=float(specs_timeout),
                write=5.0,
                pool=5.0,
            ),
        )
        # Stream chunks to avoid read timeout on large JSON responses
        content_parts: list[str] = []
        for chunk in response:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                content_parts.append(delta)
        content = "".join(content_parts).strip()
        # Strip markdown code fences if present
        content = _re.sub(r"```(?:json)?\s*", "", content).strip().rstrip("```").strip()
        if content.startswith("["):
            match_arr = _re.search(r"\[.*\]", content, flags=_re.DOTALL)
            parsed = _json.loads(match_arr.group(0) if match_arr else content)
        else:
            match_obj = _re.search(r"\{.*\}", content, flags=_re.DOTALL)
            parsed = _json.loads(match_obj.group(0) if match_obj else content)
        specs = _normalize_plan_to_specs(parsed)
        if specs:
            # Post-process: remove chart types not allowed for user's plan
            _pro_set = set(_PRO_CHART_TYPES_LIST)
            allowed_set = set(allowed_chart_types)
            specs = [
                s for s in specs
                if s.get("chart_type") in ("kpi", "heading", "text_canvas", "table") or
                s.get("chart_type") in allowed_set
            ]
            return specs
        logger.warning("AI returned a response but produced no normalizable dashboard specs.")
    except Exception:
        logger.exception(
            "AI dashboard specs generation failed (timeout=%ss); falling back to heuristic specs.",
            specs_timeout,
        )
    return None


def ai_generate_dashboard_title(df: pd.DataFrame, profile: "ProfileSummary", dataset_name: str = "") -> str | None:
    """Ask AI for a concise, business-specific dashboard title tailored to the dataset."""
    import json as _json
    import re as _re

    client, model = _get_ai_client()
    if client is None:
        return None

    date_cols = [c for c in df.columns if any(k in str(c).lower() for k in ["date", "month", "year", "period", "quarter"])]

    # Build rich statistical context so AI can infer the business domain
    sample_stats: dict = {}
    for col in profile.numeric_columns[:6]:
        try:
            s = df[col].dropna()
            sample_stats[str(col)] = {
                "sum": round(float(s.sum()), 2),
                "mean": round(float(s.mean()), 2),
                "human_label": _humanize_col(col),
            }
        except Exception:
            pass

    top_categories: dict = {}
    for col in profile.categorical_columns[:5]:
        try:
            top_categories[str(col)] = list(df[col].value_counts().head(4).index.astype(str))
        except Exception:
            pass

    sample_rows: list = []
    try:
        sample_rows = df.head(3).fillna("").astype(str).to_dict(orient="records")
    except Exception:
        pass

    payload = {
        "dataset_name": str(dataset_name or "").strip(),
        "columns": [str(c) for c in df.columns[:50]],
        "numeric_columns": [str(c) for c in profile.numeric_columns[:12]],
        "categorical_columns": [str(c) for c in profile.categorical_columns[:12]],
        "date_columns": [str(c) for c in date_cols[:5]],
        "total_rows": int(profile.total_rows),
        "sample_stats": sample_stats,
        "top_categories": top_categories,
        "sample_rows": sample_rows,
    }
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a senior BI analyst and executive dashboard strategist. "
                        "Your job is to generate ONE precise, business-oriented dashboard title that "
                        "captures the core purpose of the dataset.\n\n"
                        "Return ONLY valid JSON with a single key: {\"title\": \"Your Title Here\"}\n\n"
                        "TITLE RULES:\n"
                        "- 3-7 words, Title Case\n"
                        "- Identify the business domain from columns and sample data "
                        "(e.g. Sales, HR, Finance, Supply Chain, Marketing, Operations, E-commerce, Logistics)\n"
                        "- Include the primary entity or metric being tracked "
                        "(e.g. Revenue, Orders, Employees, Inventory, Customers, Claims)\n"
                        "- Use dataset_name as a context clue but do NOT copy it verbatim\n"
                        "- GOOD examples: 'Sales Revenue Performance', 'Employee Attrition Intelligence', "
                        "'E-commerce Order Analysis', 'Supply Chain Cost Tracker', "
                        "'Marketing Campaign ROI', 'Customer Churn Monitor'\n"
                        "- BAD examples: 'Business Overview', 'Data Analysis Dashboard', "
                        "'Analytics Report', 'Dataset Overview', 'Business Dashboard'\n"
                        "- No subtitles, no colons, no punctuation except hyphens\n"
                        "- Be decisive and specific — use the actual domain vocabulary from the columns"
                    ),
                },
                {"role": "user", "content": _json.dumps(payload)},
            ],
            temperature=0.15,
            stream=False,
            timeout=15,
        )
        content = ((response.choices[0].message.content) or "").strip()
        # Strip markdown code fences if present
        content = _re.sub(r"```(?:json)?\s*", "", content).strip().rstrip("```").strip()
        match = _re.search(r"\{.*?\}", content, flags=_re.DOTALL)
        parsed = _json.loads(match.group(0) if match else content)
        title = str(parsed.get("title", "")).strip()
        if title:
            return title[:200]
    except Exception:
        pass
    return None


def ai_generate_html_dashboard(df: pd.DataFrame, profile: "ProfileSummary", dataset_name: str = "") -> str | None:
    """Generate a complete, standalone HTML dashboard file using the configured AI provider.

    Uses DeepSeek if DEEPSEEK_API_KEY is set, otherwise Gemini if GEMINI_API_KEY is set.
    Returns a full HTML string ready to save/serve as a .html file, or None on failure.
    """
    import json as _json
    import re as _re

    client, _model = _get_ai_client()
    if client is None:
        return None

    # Build rich data context for the prompt
    columns = [str(c) for c in df.columns[:30]]
    numeric_cols = [str(c) for c in profile.numeric_columns[:10]]
    categorical_cols = [str(c) for c in profile.categorical_columns[:15]]
    date_cols = [c for c in columns if any(k in c.lower() for k in ["date", "month", "year", "period", "quarter"])]

    sample_stats: dict = {}
    for col in numeric_cols:
        try:
            s = df[col].dropna()
            sample_stats[col] = {
                "sum": round(float(s.sum()), 2),
                "mean": round(float(s.mean()), 2),
                "min": round(float(s.min()), 2),
                "max": round(float(s.max()), 2),
            }
        except Exception:
            pass

    cat_top: dict = {}
    for col in categorical_cols[:10]:
        try:
            top = df[col].value_counts().head(8)
            cat_top[col] = {str(k): int(v) for k, v in top.items()}
        except Exception:
            pass

    sample_rows: list = []
    try:
        sample_rows = df.head(5).fillna("").astype(str).to_dict(orient="records")
    except Exception:
        pass

    payload = {
        "dataset_name": str(dataset_name or "Sales Data").strip(),
        "total_rows": int(profile.total_rows),
        "columns": columns,
        "numeric_columns": numeric_cols,
        "categorical_columns": categorical_cols,
        "date_columns": date_cols,
        "sample_rows": sample_rows,
        "numeric_stats": sample_stats,
        "categorical_top_values": cat_top,
    }

    system_prompt = (
        "You are a world-class front-end developer and data visualization expert.\n"
        "Your task: generate ONE complete, self-contained HTML file for an advanced interactive dashboard.\n\n"
        "STRICT REQUIREMENTS:\n"
        "1. Return ONLY raw HTML — no markdown, no code fences, no explanation. Start with <!DOCTYPE html>.\n"
        "2. Use these CDNs (exact versions):\n"
        "   - Chart.js: https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js\n"
        "   - SheetJS: https://cdn.sheetjs.com/xlsx-0.20.2/package/dist/xlsx.full.min.js\n"
        "   - Font Awesome 6: https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css\n"
        "3. Excel upload with SheetJS: user uploads .xlsx/.xls/.csv; parse and load data dynamically.\n"
        "4. Column mapping: use flexible case-insensitive keyword matching on the exact column names from the payload.\n"
        "5. KPI cards: Total Target, Total Achievement, Achievement %, Average Achievement Rate.\n"
        "6. Interactive filter dropdowns for key categorical columns (Province, Branch, Regional Manager, Cluster, etc.).\n"
        "7. Charts (all Chart.js, all data-driven from uploaded file):\n"
        "   - Grouped bar: Top 5 branches — Achievement vs Target\n"
        "   - Horizontal bar: Province-wise achievement rate %\n"
        "   - Doughnut: Achievement % share by Regional Manager\n"
        "   - Radar: Achievement % by Cluster\n"
        "   - Line chart (if date/month/year columns exist): monthly trend of Achievement vs Target\n"
        "8. Scrollable data table showing all filtered rows with a % Achieved badge column.\n"
        "9. Modern design: glassmorphism cards, gradient header, smooth hover transitions, responsive CSS grid.\n"
        "10. Colors: indigo/blue primary, emerald for achievement, rose for shortfall, amber for neutral.\n"
        "11. All filters update KPIs, all charts, and the table simultaneously.\n"
        "12. 'Download Dashboard' button that saves the page HTML as a .html file.\n"
        "13. Footer: 'Powered by DashAI | Data stays in your browser'.\n"
        "14. Currency: LKR prefix with locale comma formatting (e.g. LKR 15,100,000).\n"
        "15. Column mapping keywords to detect from column names:\n"
        "    Province: 'province' | Branch: 'branch' | Category: 'category'\n"
        "    Regional Manager: 'regional manager' or 'rm' | Cluster: 'cluster'\n"
        "    Month: 'month' | Year: 'year' | Target: 'target' | Achievement: 'achievement'\n"
    )

    user_message = (
        f"Dataset context (use for column mapping and accurate defaults):\n"
        f"{_json.dumps(payload, indent=2, default=str)}\n\n"
        f"Generate the complete standalone HTML dashboard now. "
        f"Output ONLY raw HTML starting with <!DOCTYPE html>."
    )

    try:
        html_parts: list[str] = []
        stream = client.chat.completions.create(
            model=_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0.25,
            stream=True,
            timeout=120,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                html_parts.append(delta)

        html = "".join(html_parts).strip()
        # Strip markdown code fences if the model ignores the instruction
        html = _re.sub(r"^```(?:html)?\s*", "", html, flags=_re.IGNORECASE)
        html = _re.sub(r"\s*```$", "", html).strip()

        if html.lower().startswith("<!doctype") or html.lower().startswith("<html"):
            return html
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
        df = detect_and_clean_headers(df)
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
        "kpi_meta": {"format": "count", "icon": "people", "prefix": "", "suffix": ""},
        "layout": {"size": "sm"},
        "ai_insight": (
            f"This dataset contains {profile.total_rows:,} records across "
            f"{profile.total_columns} dimensions, providing a comprehensive analytical foundation."
        ),
    }
    kpi_rows_cfg["builder"] = _make_builder(measure="rows")
    specs.append({"title": "Total Records", "widget_type": "kpi", "config": kpi_rows_cfg, "position": position})
    position += 1

    # ── KPI 2: Sum of first numeric column ──────────────────────────────────
    if profile.suggested_measures:
        m1 = profile.suggested_measures[0]
        try:
            col_s1 = df[m1].dropna()
            total = col_s1.sum()
            trend_data = _compute_kpi_trend(df, m1)
            kpi_meta = _detect_kpi_meta(m1)
            human_m1 = _humanize_col(m1)
            prefix = kpi_meta.get("prefix", "")
            kpi_cfg: dict = {
                "kpi": f"Total {human_m1}",
                "value": f"{prefix}{total:,.0f}" if prefix else f"{total:,.0f}",
                "kpi_meta": kpi_meta,
                "layout": {"size": "sm"},
                "ai_insight": (
                    f"Total {human_m1}: {prefix}{total:,.0f} across {len(col_s1):,} records. "
                    f"Mean {prefix}{col_s1.mean():,.2f} · Median {prefix}{col_s1.median():,.2f} · "
                    f"75th percentile {prefix}{col_s1.quantile(0.75):,.2f}."
                ),
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
            col_s2 = df[m2].dropna()
            total2 = col_s2.sum()
            trend_data2 = _compute_kpi_trend(df, m2)
            kpi_meta2 = _detect_kpi_meta(m2)
            human_m2 = _humanize_col(m2)
            prefix2 = kpi_meta2.get("prefix", "")
            kpi2_cfg: dict = {
                "kpi": f"Total {human_m2}",
                "value": f"{prefix2}{total2:,.0f}" if prefix2 else f"{total2:,.0f}",
                "kpi_meta": kpi_meta2,
                "layout": {"size": "sm"},
                "ai_insight": (
                    f"Total {human_m2}: {prefix2}{total2:,.0f}. "
                    f"Average {prefix2}{col_s2.mean():,.2f} · P75 {prefix2}{col_s2.quantile(0.75):,.2f} · "
                    f"Max {prefix2}{col_s2.max():,.2f}."
                ),
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
            col_avg = df[m1].dropna()
            avg_val = col_avg.mean()
            kpi_meta_avg = _detect_kpi_meta(m1)
            human_m1 = _humanize_col(m1)
            prefix_avg = kpi_meta_avg.get("prefix", "")
            avg_cfg: dict = {
                "kpi": f"Avg {human_m1}",
                "value": f"{prefix_avg}{avg_val:,.2f}" if prefix_avg else f"{avg_val:,.2f}",
                "kpi_meta": kpi_meta_avg,
                "layout": {"size": "sm"},
                "ai_insight": (
                    f"Average {human_m1}: {prefix_avg}{avg_val:,.2f}. "
                    f"Median {prefix_avg}{col_avg.median():,.2f} · "
                    f"Range {prefix_avg}{col_avg.min():,.2f}–{prefix_avg}{col_avg.max():,.2f}."
                ),
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
            col_s3 = df[m3].dropna()
            total3 = col_s3.sum()
            trend_data3 = _compute_kpi_trend(df, m3)
            kpi_meta3 = _detect_kpi_meta(m3)
            human_m3 = _humanize_col(m3)
            prefix3 = kpi_meta3.get("prefix", "")
            kpi3_cfg: dict = {
                "kpi": f"Total {human_m3}",
                "value": f"{prefix3}{total3:,.0f}" if prefix3 else f"{total3:,.0f}",
                "kpi_meta": kpi_meta3,
                "layout": {"size": "sm"},
                "ai_insight": (
                    f"Total {human_m3}: {prefix3}{total3:,.0f}. "
                    f"Avg {prefix3}{col_s3.mean():,.2f} · P75 {prefix3}{col_s3.quantile(0.75):,.2f}."
                ),
            }
            if trend_data3:
                kpi3_cfg["trend"] = trend_data3
            kpi3_cfg["builder"] = _make_builder(measures=[m3], measure=m3)
            specs.append({"title": f"Total {human_m3}", "widget_type": "kpi", "config": kpi3_cfg, "position": position})
            position += 1
        except Exception:
            pass

    # ── KPI 5: Unique count of first categorical dimension ───────────────────
    if profile.suggested_dimensions:
        d1 = profile.suggested_dimensions[0]
        try:
            unique_count = int(df[d1].nunique(dropna=True))
            human_d1 = _humanize_col(d1)
            top_val = str(df[d1].value_counts().index[0]) if len(df[d1].value_counts()) > 0 else "N/A"
            top_cnt = int(df[d1].value_counts().values[0]) if len(df[d1].value_counts()) > 0 else 0
            kpi5_cfg: dict = {
                "kpi": f"Unique {human_d1}",
                "value": f"{unique_count:,}",
                "kpi_meta": {"format": "count", "icon": "people", "prefix": "", "suffix": ""},
                "layout": {"size": "sm"},
                "ai_insight": (
                    f"{unique_count:,} unique {human_d1} values across {profile.total_rows:,} records. "
                    f"Top category: '{top_val}' with {top_cnt:,} records "
                    f"({round(top_cnt / profile.total_rows * 100, 1)}% of total)."
                ),
            }
            kpi5_cfg["builder"] = _make_builder(dimension=d1, measure=d1)
            specs.append({"title": f"Unique {human_d1}", "widget_type": "kpi", "config": kpi5_cfg, "position": position})
            position += 1
        except Exception:
            pass

    # ── Chart 1: Bar – top dimension by first measure ────────────────────────
    if profile.suggested_dimensions and profile.suggested_measures:
        dim = profile.suggested_dimensions[0]
        measure = profile.suggested_measures[0]
        try:
            top = pd.to_numeric(df[measure], errors="coerce").groupby(df[dim]).sum().nlargest(10)
            bar_cfg = _bar_config([str(l) for l in top.index], [round(float(v), 2) for v in top.values], measure, "vibrant")
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
                line_cfg = _line_config([str(p) for p in trend.index], [round(float(v), 2) for v in trend.values], measure, "aurora")
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
                area_cfg = _area_config([str(p) for p in trend.index], [round(float(v), 2) for v in trend.values], measure, "tropical")
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
            doughnut_cfg = _doughnut_config([str(l) for l in vc2.index], [int(v) for v in vc2.values], "candy")
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
            top2 = pd.to_numeric(df[measure], errors="coerce").groupby(df[dim2]).sum().nlargest(10)
            hbar_cfg = _hbar_config([str(l) for l in top2.index], [round(float(v), 2) for v in top2.values], measure, "tropical")
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
            top_r = pd.to_numeric(df[measure], errors="coerce").groupby(df[dim]).sum().nlargest(8)
            if len(top_r) >= 3:
                radar_cfg = _radar_config([str(l) for l in top_r.index], [round(float(v), 2) for v in top_r.values], measure, "candy")
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
            top3 = pd.to_numeric(df[measure], errors="coerce").groupby(df[dim3]).sum().nlargest(8)
            bar3_cfg = _bar_config([str(l) for l in top3.index], [round(float(v), 2) for v in top3.values], measure, "aurora")
            bar3_cfg["layout"] = {"size": "md"}
            bar3_cfg["builder"] = _make_builder(dimension=dim3, measures=[measure], measure=measure)
            title = f"{_humanize_col(measure)} by {_humanize_col(dim3)}"
            specs.append({"title": title, "widget_type": "bar", "config": bar3_cfg, "position": position})
            position += 1
        except Exception:
            pass

    return specs
