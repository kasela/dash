from pathlib import Path

from django.db import transaction
from django.db.utils import OperationalError, ProgrammingError
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from apps.workspaces.models import Workspace

from .models import Dataset, DatasetColumn, DatasetVersion, ExternalDataSource
from .services import (
    ai_clean_dataframe,
    build_profile_summary,
    build_widget_suggestions,
    clean_dataframe,
    detect_external_source_type,
    fetch_from_url,
    infer_column_kind,
    parse_uploaded_file,
)


@require_GET
def dataset_link(request: HttpRequest) -> HttpResponse:
    """Render the URL-based dataset import page (Google Sheets / Excel Online)."""
    return render(request, "datasets/link.html", {})


@require_POST
def dataset_link_result(request: HttpRequest) -> HttpResponse:
    """Fetch tabular data from a pasted URL and profile it (HTMX target)."""
    url = request.POST.get("dataset_url", "").strip()
    if not url:
        return HttpResponseBadRequest("No URL provided")

    try:
        parsed = fetch_from_url(url)
    except Exception as exc:
        return render(
            request,
            "datasets/partials/link_result.html",
            {"fetch_error": str(exc), "url": url},
        )

    profile_summary = build_profile_summary(parsed.dataframe)
    widget_suggestions = build_widget_suggestions(profile_summary)

    dataset_version = None
    persistence_error = None
    plan_error = None
    if request.user.is_authenticated:
        try:
            from apps.billing.models import UserProfile
            from django.utils import timezone
            profile, _ = UserProfile.objects.get_or_create(user=request.user)
            if not profile.is_pro:
                from django.utils import timezone as tz
                month_start = tz.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                monthly_count = DatasetVersion.objects.filter(
                    dataset__workspace__owner=request.user,
                    uploaded_at__gte=month_start,
                ).count()
                if monthly_count >= profile.max_monthly_uploads:
                    plan_error = (
                        f"You've reached the {profile.max_monthly_uploads} upload/month limit on the Free plan."
                    )
                else:
                    dataset_version = _persist_dataset_from_url(request, url, parsed)
            else:
                dataset_version = _persist_dataset_from_url(request, url, parsed)
        except Exception as exc:
            persistence_error = str(exc)

    context = {
        "headers": parsed.headers,
        "rows": parsed.rows,
        "shape": parsed.shape,
        "url": url,
        "dataset_version": dataset_version,
        "persistence_error": persistence_error,
        "plan_error": plan_error,
        "profile": profile_summary,
        "widget_suggestions": widget_suggestions,
    }
    return render(request, "datasets/partials/link_result.html", context)


@transaction.atomic
def _persist_dataset_from_url(request: HttpRequest, url: str, parsed):
    """Save a DataFrame fetched from a URL as a Dataset + DatasetVersion."""
    import io
    from django.core.files.uploadedfile import InMemoryUploadedFile

    workspace, _ = Workspace.objects.get_or_create(
        owner=request.user,
        name=f"{request.user.username}'s Workspace",
    )

    # Derive a name from the URL
    from urllib.parse import urlparse
    parsed_url = urlparse(url)
    if "docs.google.com" in parsed_url.netloc:
        source_name = "Google Sheets Import"
    elif "onedrive" in parsed_url.netloc or "sharepoint" in parsed_url.netloc:
        source_name = "Excel Online Import"
    else:
        path_part = parsed_url.path.rstrip("/").rsplit("/", 1)[-1]
        source_name = path_part[:200] or "URL Import"

    dataset, created = Dataset.objects.get_or_create(workspace=workspace, name=source_name)

    # Store source-type metadata
    source_type = detect_external_source_type(url)
    ExternalDataSource.objects.update_or_create(
        dataset=dataset,
        defaults={"source_type": source_type, "original_url": url},
    )

    # Serialize DataFrame to CSV and wrap as InMemoryUploadedFile
    csv_bytes = parsed.dataframe.to_csv(index=False).encode("utf-8")
    csv_file = InMemoryUploadedFile(
        file=io.BytesIO(csv_bytes),
        field_name="source_file",
        name=f"{source_name}.csv",
        content_type="text/csv",
        size=len(csv_bytes),
        charset="utf-8",
    )

    next_version = dataset.versions.count() + 1
    dataset_version = DatasetVersion.objects.create(
        dataset=dataset,
        version=next_version,
        source_file=csv_file,
        row_count=parsed.shape[0],
        column_count=parsed.shape[1],
    )

    for column_name in parsed.headers:
        series = parsed.dataframe[column_name]
        DatasetColumn.objects.create(
            dataset_version=dataset_version,
            name=column_name,
            kind=infer_column_kind(series),
            dtype=str(series.dtype),
            null_ratio=float(series.isna().mean()),
        )

    return dataset_version


@require_GET
def dataset_upload(request: HttpRequest) -> HttpResponse:
    file_formats = [
        {"ext": "CSV", "name": "CSV files", "desc": "Comma-separated values", "color": "bg-emerald-500"},
        {"ext": "XLS", "name": "Excel files", "desc": ".xlsx and .xlsm", "color": "bg-blue-500"},
        {"ext": "JSON", "name": "JSON files", "desc": "Flat or nested arrays", "color": "bg-amber-500"},
    ]
    return render(request, "datasets/upload.html", {"file_formats": file_formats})


@require_POST
def dataset_upload_result(request: HttpRequest) -> HttpResponse:
    upload = request.FILES.get("dataset_file")
    if upload is None:
        return HttpResponseBadRequest("No file uploaded")

    try:
        parsed = parse_uploaded_file(upload)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    profile_summary = build_profile_summary(parsed.dataframe)
    widget_suggestions = build_widget_suggestions(profile_summary)

    dataset_version = None
    persistence_error = None
    plan_error = None
    if request.user.is_authenticated:
        try:
            from apps.billing.models import UserProfile
            from django.utils import timezone
            profile, _ = UserProfile.objects.get_or_create(user=request.user)
            if not profile.is_pro:
                # Count uploads this calendar month
                from apps.datasets.models import DatasetVersion as DV
                month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                monthly_count = DV.objects.filter(
                    dataset__workspace__owner=request.user,
                    uploaded_at__gte=month_start,
                ).count()
                if monthly_count >= profile.max_monthly_uploads:
                    plan_error = f"You've reached the {profile.max_monthly_uploads} upload/month limit on the Free plan."
                    dataset_version = None
                else:
                    dataset_version = _persist_dataset_for_user(request, upload, parsed)
            else:
                dataset_version = _persist_dataset_for_user(request, upload, parsed)
        except (OperationalError, ProgrammingError):
            persistence_error = "Database tables are not ready yet. Run: python manage.py migrate"

    context = {
        "headers": parsed.headers,
        "rows": parsed.rows,
        "shape": parsed.shape,
        "filename": upload.name,
        "dataset_version": dataset_version,
        "persistence_error": persistence_error,
        "plan_error": plan_error,
        "profile": profile_summary,
        "widget_suggestions": widget_suggestions,
    }
    return render(request, "datasets/partials/upload_result.html", context)


@require_POST
def dataset_clean_version(request: HttpRequest, version_id: int) -> HttpResponse:
    """Apply cleaning operations to an existing DatasetVersion and save a new version."""
    import io
    import pandas as pd
    from django.core.files.uploadedfile import InMemoryUploadedFile
    from django.shortcuts import get_object_or_404

    version = get_object_or_404(DatasetVersion, pk=version_id)

    # Load the DataFrame from the stored file
    try:
        file_name = version.source_file.name.lower()
        if file_name.endswith(".csv"):
            df = pd.read_csv(version.source_file)
        elif file_name.endswith((".xlsx", ".xlsm")):
            df = pd.read_excel(version.source_file)
        elif file_name.endswith(".json"):
            df = pd.read_json(version.source_file)
        else:
            df = pd.read_csv(version.source_file)
    except Exception as exc:
        return HttpResponseBadRequest(f"Could not read dataset file: {exc}")

    drop_duplicates = request.POST.get("drop_duplicates") == "on"
    missing_strategy = request.POST.get("missing_strategy", "keep")
    if missing_strategy not in ("keep", "drop_rows", "fill_zero", "fill_mean"):
        missing_strategy = "keep"

    clean_result = clean_dataframe(df, drop_duplicates=drop_duplicates, missing_strategy=missing_strategy)
    cleaned_df = clean_result.dataframe

    # Save new DatasetVersion with cleaned data
    csv_bytes = cleaned_df.to_csv(index=False).encode("utf-8")
    import os
    original_name = os.path.basename(version.source_file.name)
    stem = os.path.splitext(original_name)[0]
    csv_file = InMemoryUploadedFile(
        file=io.BytesIO(csv_bytes),
        field_name="source_file",
        name=f"{stem}_cleaned.csv",
        content_type="text/csv",
        size=len(csv_bytes),
        charset="utf-8",
    )

    with transaction.atomic():
        next_version = version.dataset.versions.count() + 1
        new_version = DatasetVersion.objects.create(
            dataset=version.dataset,
            version=next_version,
            source_file=csv_file,
            row_count=int(cleaned_df.shape[0]),
            column_count=int(cleaned_df.shape[1]),
        )
        for column_name in cleaned_df.columns:
            series = cleaned_df[column_name]
            DatasetColumn.objects.create(
                dataset_version=new_version,
                name=str(column_name),
                kind=infer_column_kind(series),
                dtype=str(series.dtype),
                null_ratio=float(series.isna().mean()),
            )

    sample_df = cleaned_df.head(100)
    records = sample_df.where(pd.notnull(sample_df), None).to_dict(orient="records")
    profile_summary = build_profile_summary(cleaned_df)
    widget_suggestions = build_widget_suggestions(profile_summary)

    context = {
        "headers": [str(h) for h in cleaned_df.columns],
        "rows": records,
        "shape": cleaned_df.shape,
        "filename": f"{stem}_cleaned.csv",
        "dataset_version": new_version,
        "persistence_error": None,
        "plan_error": None,
        "profile": profile_summary,
        "widget_suggestions": widget_suggestions,
        "clean_result": clean_result,
    }
    return render(request, "datasets/partials/upload_result.html", context)


@require_POST
def dataset_delete_rows(request: HttpRequest, version_id: int) -> HttpResponse:
    """Delete user-selected rows from an existing DatasetVersion and save as a new version."""
    import io
    import json
    import pandas as pd
    from django.core.files.uploadedfile import InMemoryUploadedFile
    from django.shortcuts import get_object_or_404

    version = get_object_or_404(DatasetVersion, pk=version_id)

    raw_indices = request.POST.get("row_indices", "")
    try:
        row_indices = json.loads(raw_indices) if raw_indices else []
        row_indices = [int(i) for i in row_indices]
    except (ValueError, TypeError):
        return HttpResponseBadRequest("Invalid row indices")

    if not row_indices:
        return HttpResponseBadRequest("No rows selected for deletion")

    # Load the DataFrame
    try:
        file_name = version.source_file.name.lower()
        if file_name.endswith(".csv"):
            df = pd.read_csv(version.source_file)
        elif file_name.endswith((".xlsx", ".xlsm")):
            df = pd.read_excel(version.source_file)
        elif file_name.endswith(".json"):
            df = pd.read_json(version.source_file)
        else:
            df = pd.read_csv(version.source_file)
    except Exception as exc:
        return HttpResponseBadRequest(f"Could not read dataset file: {exc}")

    rows_before = len(df)
    # Validate indices are in range
    valid_indices = [i for i in row_indices if 0 <= i < len(df)]
    if not valid_indices:
        return HttpResponseBadRequest("Selected row indices are out of range")

    cleaned_df = df.drop(index=valid_indices).reset_index(drop=True)
    rows_removed = rows_before - len(cleaned_df)

    # Save new DatasetVersion
    import os
    original_name = os.path.basename(version.source_file.name)
    stem = os.path.splitext(original_name)[0]
    csv_bytes = cleaned_df.to_csv(index=False).encode("utf-8")
    csv_file = InMemoryUploadedFile(
        file=io.BytesIO(csv_bytes),
        field_name="source_file",
        name=f"{stem}_filtered.csv",
        content_type="text/csv",
        size=len(csv_bytes),
        charset="utf-8",
    )

    with transaction.atomic():
        next_version = version.dataset.versions.count() + 1
        new_version = DatasetVersion.objects.create(
            dataset=version.dataset,
            version=next_version,
            source_file=csv_file,
            row_count=int(cleaned_df.shape[0]),
            column_count=int(cleaned_df.shape[1]),
        )
        for column_name in cleaned_df.columns:
            series = cleaned_df[column_name]
            DatasetColumn.objects.create(
                dataset_version=new_version,
                name=str(column_name),
                kind=infer_column_kind(series),
                dtype=str(series.dtype),
                null_ratio=float(series.isna().mean()),
            )

    sample_df = cleaned_df.head(100)
    records = sample_df.where(pd.notnull(sample_df), None).to_dict(orient="records")
    profile_summary = build_profile_summary(cleaned_df)
    widget_suggestions = build_widget_suggestions(profile_summary)

    context = {
        "headers": [str(h) for h in cleaned_df.columns],
        "rows": records,
        "shape": cleaned_df.shape,
        "filename": f"{stem}_filtered.csv",
        "dataset_version": new_version,
        "persistence_error": None,
        "plan_error": None,
        "profile": profile_summary,
        "widget_suggestions": widget_suggestions,
        "rows_deleted_count": rows_removed,
    }
    return render(request, "datasets/partials/upload_result.html", context)


@require_POST
def dataset_ai_clean(request: HttpRequest, version_id: int) -> HttpResponse:
    """Run AI-powered cleaning on an existing DatasetVersion and save as a new version."""
    import io
    import pandas as pd
    from django.core.files.uploadedfile import InMemoryUploadedFile
    from django.shortcuts import get_object_or_404

    version = get_object_or_404(DatasetVersion, pk=version_id)

    try:
        file_name = version.source_file.name.lower()
        if file_name.endswith(".csv"):
            df = pd.read_csv(version.source_file)
        elif file_name.endswith((".xlsx", ".xlsm")):
            df = pd.read_excel(version.source_file)
        elif file_name.endswith(".json"):
            df = pd.read_json(version.source_file)
        else:
            df = pd.read_csv(version.source_file)
    except Exception as exc:
        return HttpResponseBadRequest(f"Could not read dataset file: {exc}")

    cleaned_df, ai_report = ai_clean_dataframe(df)

    import os
    original_name = os.path.basename(version.source_file.name)
    stem = os.path.splitext(original_name)[0]
    csv_bytes = cleaned_df.to_csv(index=False).encode("utf-8")
    csv_file = InMemoryUploadedFile(
        file=io.BytesIO(csv_bytes),
        field_name="source_file",
        name=f"{stem}_ai_cleaned.csv",
        content_type="text/csv",
        size=len(csv_bytes),
        charset="utf-8",
    )

    with transaction.atomic():
        next_version = version.dataset.versions.count() + 1
        new_version = DatasetVersion.objects.create(
            dataset=version.dataset,
            version=next_version,
            source_file=csv_file,
            row_count=int(cleaned_df.shape[0]),
            column_count=int(cleaned_df.shape[1]),
        )
        for column_name in cleaned_df.columns:
            series = cleaned_df[column_name]
            DatasetColumn.objects.create(
                dataset_version=new_version,
                name=str(column_name),
                kind=infer_column_kind(series),
                dtype=str(series.dtype),
                null_ratio=float(series.isna().mean()),
            )

    sample_df = cleaned_df.head(100)
    records = sample_df.where(pd.notnull(sample_df), None).to_dict(orient="records")
    profile_summary = build_profile_summary(cleaned_df)
    widget_suggestions = build_widget_suggestions(profile_summary)

    context = {
        "headers": [str(h) for h in cleaned_df.columns],
        "rows": records,
        "shape": cleaned_df.shape,
        "filename": f"{stem}_ai_cleaned.csv",
        "dataset_version": new_version,
        "persistence_error": None,
        "plan_error": None,
        "profile": profile_summary,
        "widget_suggestions": widget_suggestions,
        "ai_clean_report": ai_report,
    }
    return render(request, "datasets/partials/upload_result.html", context)


@transaction.atomic
def _persist_dataset_for_user(request: HttpRequest, upload, parsed):
    workspace, _ = Workspace.objects.get_or_create(
        owner=request.user,
        name=f"{request.user.username}'s Workspace",
    )

    dataset_name = Path(upload.name).stem[:200] or "Dataset"
    dataset, _ = Dataset.objects.get_or_create(
        workspace=workspace,
        name=dataset_name,
    )

    next_version = dataset.versions.count() + 1
    dataset_version = DatasetVersion.objects.create(
        dataset=dataset,
        version=next_version,
        source_file=upload,
        row_count=parsed.shape[0],
        column_count=parsed.shape[1],
    )

    for column_name in parsed.headers:
        series = parsed.dataframe[column_name]
        DatasetColumn.objects.create(
            dataset_version=dataset_version,
            name=column_name,
            kind=infer_column_kind(series),
            dtype=str(series.dtype),
            null_ratio=float(series.isna().mean()),
        )

    return dataset_version
