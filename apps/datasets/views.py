from pathlib import Path

from django.db import transaction
from django.db.utils import OperationalError, ProgrammingError
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from apps.workspaces.models import Workspace

from .models import Dataset, DatasetColumn, DatasetVersion
from .services import (
    build_profile_summary,
    build_widget_suggestions,
    infer_column_kind,
    parse_uploaded_file,
)


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
                from django.db.models.functions import TruncMonth
                month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                monthly_count = DV.objects.filter(
                    dataset__workspace__owner=request.user,
                    created_at__gte=month_start,
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
