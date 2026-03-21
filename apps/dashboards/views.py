from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


SAMPLE_CHART = {
    "type": "bar",
    "data": {
        "labels": ["North", "South", "East", "West"],
        "datasets": [{"label": "Revenue", "data": [120, 95, 135, 88]}],
    },
    "options": {"responsive": True, "maintainAspectRatio": False},
}


def dashboard_home(request: HttpRequest) -> HttpResponse:
    return render(request, "dashboards/home.html", {"chart_config": SAMPLE_CHART})
