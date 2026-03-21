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


# ── Chart palette ─────────────────────────────────────────────────────────────

_PALETTE = ["#6366f1", "#8b5cf6", "#a78bfa", "#c4b5fd", "#818cf8", "#4f46e5", "#7c3aed", "#9061f9", "#a855f7", "#d946ef"]
_CHART_SCALE_OPTS = {
    "x": {"grid": {"display": False}},
    "y": {"grid": {"color": "rgba(0,0,0,0.05)"}, "ticks": {"color": "#94a3b8"}},
}


def _bar_config(labels: list, values: list, label: str) -> dict:
    return {
        "type": "bar",
        "data": {
            "labels": labels,
            "datasets": [{
                "label": label,
                "data": values,
                "backgroundColor": _PALETTE[:len(labels)],
                "borderRadius": 6,
            }],
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "plugins": {"legend": {"display": False}},
            "scales": _CHART_SCALE_OPTS,
        },
    }


def _line_config(labels: list, values: list, label: str) -> dict:
    return {
        "type": "line",
        "data": {
            "labels": labels,
            "datasets": [{
                "label": label,
                "data": values,
                "borderColor": "#6366f1",
                "backgroundColor": "rgba(99,102,241,0.1)",
                "tension": 0.4,
                "fill": True,
                "pointRadius": 3,
            }],
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "plugins": {"legend": {"display": False}},
            "scales": _CHART_SCALE_OPTS,
        },
    }


def _pie_config(labels: list, values: list) -> dict:
    return {
        "type": "pie",
        "data": {
            "labels": labels,
            "datasets": [{
                "data": values,
                "backgroundColor": _PALETTE[:len(labels)],
                "hoverOffset": 6,
            }],
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "plugins": {"legend": {"position": "bottom", "labels": {"font": {"size": 11}, "color": "#64748b"}}},
        },
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
