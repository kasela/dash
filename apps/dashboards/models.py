import uuid
from django.db import models

from apps.datasets.models import DatasetVersion
from apps.workspaces.models import Workspace


class Dashboard(models.Model):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="dashboards")
    dataset_version = models.ForeignKey(
        DatasetVersion, on_delete=models.SET_NULL, null=True, blank=True, related_name="dashboards"
    )
    title = models.CharField(max_length=200)
    is_public = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    # filter_config: list of {id, column, filter_type, label, version_id}
    filter_config = models.JSONField(default=list, blank=True)


class DashboardDataset(models.Model):
    """Links multiple DatasetVersions to a single Dashboard."""

    dashboard = models.ForeignKey(Dashboard, on_delete=models.CASCADE, related_name="dataset_links")
    dataset_version = models.ForeignKey(DatasetVersion, on_delete=models.CASCADE, related_name="dashboard_links")
    label = models.CharField(max_length=100, blank=True)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("dashboard", "dataset_version")]


class DashboardWidget(models.Model):
    class WidgetType(models.TextChoices):
        KPI = "kpi", "KPI"
        BAR = "bar", "Bar"
        LINE = "line", "Line"
        PIE = "pie", "Pie"
        DOUGHNUT = "doughnut", "Doughnut"
        AREA = "area", "Area"
        HBAR = "hbar", "Horiz. Bar"
        SCATTER = "scatter", "Scatter"
        RADAR = "radar", "Radar"
        TABLE = "table", "Table"
        HEADING = "heading", "Heading"
        TEXT_CANVAS = "text_canvas", "Text Canvas"
        # Pro / Plus chart types
        BUBBLE = "bubble", "Bubble"
        POLARAREA = "polararea", "Polar Area"
        MIXED = "mixed", "Mixed"
        FUNNEL = "funnel", "Funnel"
        GAUGE = "gauge", "Gauge"
        WATERFALL = "waterfall", "Waterfall"
        MAP = "map", "Map"

    dashboard = models.ForeignKey(Dashboard, on_delete=models.CASCADE, related_name="widgets")
    source_dataset_version = models.ForeignKey(
        DatasetVersion, on_delete=models.SET_NULL, null=True, blank=True, related_name="widget_uses"
    )
    title = models.CharField(max_length=200)
    widget_type = models.CharField(max_length=16, choices=WidgetType.choices)
    position = models.PositiveIntegerField(default=0)
    chart_config = models.JSONField(default=dict)



class DashboardShareLink(models.Model):
    dashboard = models.ForeignKey(Dashboard, on_delete=models.CASCADE, related_name="share_links")
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
