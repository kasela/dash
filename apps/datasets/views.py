from pathlib import Path

from django.db import transaction
from django.db.utils import OperationalError, ProgrammingError
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from apps.workspaces.models import Workspace

from .models import Dataset, DatasetColumn, DatasetVersion, ExternalDataSource
from .services import (
    build_profile_summary,
    build_widget_suggestions,
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
