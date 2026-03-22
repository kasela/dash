from django.contrib import admin

from .models import Dashboard, DashboardDataset, DashboardShareLink, DashboardWidget


class DashboardDatasetInline(admin.TabularInline):
    model = DashboardDataset
    extra = 0
    autocomplete_fields = ("dataset_version",)


class DashboardWidgetInline(admin.TabularInline):
    model = DashboardWidget
    extra = 0
    fields = ("title", "widget_type", "position", "source_dataset_version")
    autocomplete_fields = ("source_dataset_version",)


class DashboardShareLinkInline(admin.TabularInline):
    model = DashboardShareLink
    extra = 0
    readonly_fields = ("token", "created_at")


@admin.register(Dashboard)
class DashboardAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "workspace", "dataset_version", "is_public", "build_status", "created_at")
    list_filter = ("is_public", "build_status", "created_at")
    search_fields = ("title", "workspace__name", "workspace__owner__username")
    autocomplete_fields = ("workspace", "dataset_version")
    inlines = (DashboardDatasetInline, DashboardWidgetInline, DashboardShareLinkInline)


@admin.register(DashboardDataset)
class DashboardDatasetAdmin(admin.ModelAdmin):
    list_display = ("id", "dashboard", "dataset_version", "label", "added_at")
    list_filter = ("added_at",)
    search_fields = ("dashboard__title", "dataset_version__dataset__name", "label")
    autocomplete_fields = ("dashboard", "dataset_version")


@admin.register(DashboardWidget)
class DashboardWidgetAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "dashboard", "widget_type", "position", "source_dataset_version")
    list_filter = ("widget_type",)
    search_fields = ("title", "dashboard__title")
    autocomplete_fields = ("dashboard", "source_dataset_version")


@admin.register(DashboardShareLink)
class DashboardShareLinkAdmin(admin.ModelAdmin):
    list_display = ("id", "dashboard", "token", "is_active", "created_at")
    list_filter = ("is_active", "created_at")
    search_fields = ("dashboard__title", "token")
    autocomplete_fields = ("dashboard",)
    readonly_fields = ("token", "created_at")
