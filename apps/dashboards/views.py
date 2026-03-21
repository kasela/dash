from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render

from apps.datasets.models import DatasetVersion

from .models import Dashboard, DashboardWidget

SAMPLE_CHART = {
    "type": "bar",
    "data": {
        "labels": ["North", "South", "East", "West"],
        "datasets": [{"label": "Revenue", "data": [120, 95, 135, 88]}],
    },
    "options": {"responsive": True, "maintainAspectRatio": False},
}


def dashboard_home(request: HttpRequest) -> HttpResponse:
    recent_dashboards = []
    if request.user.is_authenticated:
        recent_dashboards = Dashboard.objects.filter(workspace__owner=request.user).order_by("-created_at")[:5]

    return render(
        request,
        "dashboards/home.html",
        {"chart_config": SAMPLE_CHART, "recent_dashboards": recent_dashboards},
    )


@login_required
def dashboard_create_from_version(request: HttpRequest, version_id: int) -> HttpResponse:
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    dataset_version = get_object_or_404(
        DatasetVersion,
        id=version_id,
        dataset__workspace__owner=request.user,
    )

    dashboard = Dashboard.objects.create(
        workspace=dataset_version.dataset.workspace,
        dataset_version=dataset_version,
        title=f"{dataset_version.dataset.name} Overview",
    )

    DashboardWidget.objects.create(
        dashboard=dashboard,
        title="Overview KPI",
        widget_type=DashboardWidget.WidgetType.KPI,
        position=1,
        chart_config={"kpi": "total_rows", "value": dataset_version.row_count},
    )
    DashboardWidget.objects.create(
        dashboard=dashboard,
        title="Top Categories",
        widget_type=DashboardWidget.WidgetType.BAR,
        position=2,
        chart_config=SAMPLE_CHART,
    )

    return redirect("dashboard-home")
