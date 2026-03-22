from django.db import models

from apps.workspaces.models import Workspace


class Dataset(models.Model):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="datasets")
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)


class DatasetVersion(models.Model):
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="versions")
    version = models.PositiveIntegerField(default=1)
    source_file = models.FileField(upload_to="datasets/%Y/%m/%d")
    row_count = models.PositiveIntegerField(default=0)
    column_count = models.PositiveIntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)


class ExternalDataSource(models.Model):
    """Tracks the original URL for datasets imported from Google Sheets or Excel Online."""

    class SourceType(models.TextChoices):
        GOOGLE_SHEETS = "google_sheets", "Google Sheets"
        EXCEL_ONLINE = "excel_online", "Excel Online"
        DIRECT_URL = "direct_url", "Direct URL"

    dataset = models.OneToOneField(Dataset, on_delete=models.CASCADE, related_name="external_source")
    source_type = models.CharField(max_length=32, choices=SourceType.choices, default=SourceType.DIRECT_URL)
    original_url = models.URLField(max_length=2000)
    last_synced_at = models.DateTimeField(null=True, blank=True)


class DatasetColumn(models.Model):
    class Kind(models.TextChoices):
        DIMENSION = "dimension", "Dimension"
        MEASURE = "measure", "Measure"
        DATE = "date", "Date"
        ID = "id", "ID"
        UNKNOWN = "unknown", "Unknown"

    dataset_version = models.ForeignKey(DatasetVersion, on_delete=models.CASCADE, related_name="columns")
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.UNKNOWN)
    dtype = models.CharField(max_length=64, default="object")
    null_ratio = models.FloatField(default=0)
