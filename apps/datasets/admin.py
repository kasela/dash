from django.contrib import admin

from .models import Dataset, DatasetColumn, DatasetVersion, ExternalDataSource


class DatasetVersionInline(admin.TabularInline):
    model = DatasetVersion
    extra = 0
    fields = ("version", "source_file", "row_count", "column_count", "uploaded_at")
    readonly_fields = ("uploaded_at",)


@admin.register(Dataset)
class DatasetAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "workspace", "created_at")
    list_filter = ("created_at", "workspace")
    search_fields = ("name", "workspace__name", "workspace__owner__username")
    autocomplete_fields = ("workspace",)
    inlines = (DatasetVersionInline,)


@admin.register(DatasetVersion)
class DatasetVersionAdmin(admin.ModelAdmin):
    list_display = ("id", "dataset", "version", "row_count", "column_count", "uploaded_at")
    list_filter = ("uploaded_at",)
    search_fields = ("dataset__name",)
    autocomplete_fields = ("dataset",)


@admin.register(ExternalDataSource)
class ExternalDataSourceAdmin(admin.ModelAdmin):
    list_display = ("id", "dataset", "source_type", "last_synced_at")
    list_filter = ("source_type", "last_synced_at")
    search_fields = ("dataset__name", "original_url")
    autocomplete_fields = ("dataset",)


@admin.register(DatasetColumn)
class DatasetColumnAdmin(admin.ModelAdmin):
    list_display = ("id", "dataset_version", "name", "kind", "dtype", "null_ratio")
    list_filter = ("kind", "dtype")
    search_fields = ("name", "dataset_version__dataset__name")
    autocomplete_fields = ("dataset_version",)
