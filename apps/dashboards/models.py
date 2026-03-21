from django.db import models

from apps.datasets.models import DatasetVersion
from apps.workspaces.models import Workspace


class Dashboard(models.Model):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="dashboards")
    dataset_version = models.ForeignKey(DatasetVersion, on_delete=models.CASCADE, related_name="dashboards")
    title = models.CharField(max_length=200)
    is_public = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class DashboardWidget(models.Model):
    class WidgetType(models.TextChoices):
        KPI = "kpi", "KPI"
        BAR = "bar", "Bar"
        LINE = "line", "Line"
        PIE = "pie", "Pie"
        TABLE = "table", "Table"

    dashboard = models.ForeignKey(Dashboard, on_delete=models.CASCADE, related_name="widgets")
    title = models.CharField(max_length=200)
    widget_type = models.CharField(max_length=16, choices=WidgetType.choices)
    position = models.PositiveIntegerField(default=0)
    chart_config = models.JSONField(default=dict)
