from pathlib import Path

from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from apps.workspaces.models import Workspace

from .models import Dataset, DatasetColumn, DatasetVersion
from .services import infer_column_kind, parse_uploaded_file


@require_GET
def dataset_upload(request: HttpRequest) -> HttpResponse:
    return render(request, "datasets/upload.html")


@require_POST
def dataset_upload_result(request: HttpRequest) -> HttpResponse:
    upload = request.FILES.get("dataset_file")
    if upload is None:
        return HttpResponseBadRequest("No file uploaded")

    try:
        parsed = parse_uploaded_file(upload)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    dataset_version = None
    if request.user.is_authenticated:
        dataset_version = _persist_dataset_for_user(request, upload, parsed)

    context = {
        "headers": parsed.headers,
        "rows": parsed.rows,
        "shape": parsed.shape,
        "filename": upload.name,
        "dataset_version": dataset_version,
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
