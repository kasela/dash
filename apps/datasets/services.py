from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class ParsedPreview:
    headers: list[str]
    rows: list[dict[str, object]]
    shape: tuple[int, int]


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
    return ParsedPreview(headers=[str(h) for h in sample_df.columns], rows=records, shape=df.shape)


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
