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



def build_widget_suggestions(profile: ProfileSummary) -> list[str]:
    suggestions: list[str] = []

    if profile.suggested_measures and profile.suggested_dimensions:
        dim = profile.suggested_dimensions[0]
        measure = profile.suggested_measures[0]
        suggestions.append(f"{measure} by {dim}")

    if profile.suggested_measures:
        measure = profile.suggested_measures[0]
        suggestions.append(f"Top 10 categories by {measure}")

    date_like_dims = [d for d in profile.suggested_dimensions if any(k in d.lower() for k in ["date", "month", "year", "period", "quarter"])]
    if date_like_dims and profile.suggested_measures:
        suggestions.append(f"Trend of {profile.suggested_measures[0]} by {date_like_dims[0]}")

    if profile.duplicate_rows > 0:
        suggestions.append("Data quality: duplicate records overview")

    if profile.missing_cells > 0:
        suggestions.append("Data quality: missing values by column")

    if not suggestions:
        suggestions.append("Overview KPI dashboard")

    return suggestions[:6]
