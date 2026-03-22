from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path
from django.http import HttpResponse

from apps.dashboards.views import (
    app_home,
    dashboard_add_dataset,
    dashboard_add_widget,
    dashboard_add_heading,
    dashboard_add_text_canvas,
    dashboard_add_divider,
    dashboard_build_status,
    dashboard_create_from_version,
    dashboard_create_share_link,
    dashboard_delete_widget,
    dashboard_detail,
    dashboard_get_columns,
    dashboard_list_datasets,
    dashboard_public_view,
    dashboard_remove_dataset,
    dashboard_rename,
    dashboard_reorder_widgets,
    dashboard_resize_widget,
    dashboard_rename_widget,
    dashboard_update_heading,
    dashboard_update_text_canvas,
    dashboard_update_widget,
    dashboard_update_widget_span,
    dashboard_save_filters,
    dashboard_apply_filters,
    dashboard_get_filter_columns,
    dashboard_ai_analyze_widget,
    dashboard_ai_suggest_slicers,
    dashboard_ai_clean_dataset,
    dashboard_ai_executive_summary,
    landing_page,
    pricing_page,
)
from apps.datasets.views import dataset_ai_clean, dataset_clean_version, dataset_delete_rows, dataset_link, dataset_link_result, dataset_upload, dataset_upload_result
from apps.seo.sitemaps import StaticViewSitemap


def robots_txt(request):
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        "Disallow: /app/",
        "Disallow: /datasets/",
        "Disallow: /dashboards/share/",
        "",
        "Sitemap: https://dashai.io/sitemap.xml",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")


def service_worker_js(request):
    # Return a no-op worker to avoid noisy 404s when browsers probe /service-worker.js.
    return HttpResponse("self.addEventListener('install', () => self.skipWaiting());", content_type="application/javascript")


sitemaps = {"static": StaticViewSitemap}

urlpatterns = [
    path("admin/", admin.site.urls),
    path("admin-portal/", include("apps.admin_portal.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("billing/", include("apps.billing.urls")),

    # Marketing pages
    path("", landing_page, name="landing"),
    path("pricing/", pricing_page, name="pricing"),

    # SEO
    path("robots.txt", robots_txt, name="robots-txt"),
    path("service-worker.js", service_worker_js, name="service-worker"),
    path("sitemap.xml", sitemap, {"sitemaps": sitemaps}, name="sitemap"),

    # Authenticated app
    path("app/", app_home, name="app-home"),
    path("app/dashboards/<uuid:dashboard_id>/", dashboard_detail, name="dashboard-detail"),

    # Dashboard actions
    path("dashboards/create/<int:version_id>/", dashboard_create_from_version, name="dashboard-create-from-version"),
    path("dashboards/<uuid:dashboard_id>/share/", dashboard_create_share_link, name="dashboard-create-share"),
    path("dashboards/share/<uuid:token>/", dashboard_public_view, name="dashboard-public-view"),
    path("dashboards/<uuid:dashboard_id>/build-status/", dashboard_build_status, name="dashboard-build-status"),
    path("dashboards/<uuid:dashboard_id>/columns/", dashboard_get_columns, name="dashboard-get-columns"),
    path("dashboards/<uuid:dashboard_id>/widgets/add/", dashboard_add_widget, name="dashboard-add-widget"),
    path("dashboards/<uuid:dashboard_id>/widgets/add-heading/", dashboard_add_heading, name="dashboard-add-heading"),
    path("dashboards/<uuid:dashboard_id>/widgets/add-text-canvas/", dashboard_add_text_canvas, name="dashboard-add-text-canvas"),
    path("dashboards/<uuid:dashboard_id>/widgets/add-divider/", dashboard_add_divider, name="dashboard-add-divider"),
    path("dashboards/<uuid:dashboard_id>/widgets/reorder/", dashboard_reorder_widgets, name="dashboard-reorder-widgets"),
    path("dashboards/<uuid:dashboard_id>/rename/", dashboard_rename, name="dashboard-rename"),
    path("dashboards/<uuid:dashboard_id>/widgets/<uuid:widget_id>/delete/", dashboard_delete_widget, name="dashboard-delete-widget"),
    path("dashboards/<uuid:dashboard_id>/widgets/<uuid:widget_id>/rename/", dashboard_rename_widget, name="dashboard-rename-widget"),
    path("dashboards/<uuid:dashboard_id>/widgets/<uuid:widget_id>/update/", dashboard_update_widget, name="dashboard-update-widget"),
    path("dashboards/<uuid:dashboard_id>/widgets/<uuid:widget_id>/resize/", dashboard_resize_widget, name="dashboard-resize-widget"),
    path("dashboards/<uuid:dashboard_id>/widgets/<uuid:widget_id>/span/", dashboard_update_widget_span, name="dashboard-widget-span"),
    path("dashboards/<uuid:dashboard_id>/widgets/<uuid:widget_id>/update-heading/", dashboard_update_heading, name="dashboard-update-heading"),
    path("dashboards/<uuid:dashboard_id>/widgets/<uuid:widget_id>/update-text-canvas/", dashboard_update_text_canvas, name="dashboard-update-text-canvas"),

    # Dataset actions
    path("datasets/upload/", dataset_upload, name="dataset-upload"),
    path("datasets/upload/result/", dataset_upload_result, name="dataset-upload-result"),
    path("datasets/link/", dataset_link, name="dataset-link"),
    path("datasets/link/result/", dataset_link_result, name="dataset-link-result"),
    path("datasets/versions/<int:version_id>/clean/", dataset_clean_version, name="dataset-clean-version"),
    path("datasets/versions/<int:version_id>/ai-clean/", dataset_ai_clean, name="dataset-ai-clean"),
    path("datasets/versions/<int:version_id>/delete-rows/", dataset_delete_rows, name="dataset-delete-rows"),

    # Dashboard multi-dataset management
    path("dashboards/<uuid:dashboard_id>/datasets/", dashboard_list_datasets, name="dashboard-list-datasets"),
    path("dashboards/<uuid:dashboard_id>/datasets/add/", dashboard_add_dataset, name="dashboard-add-dataset"),
    path("dashboards/<uuid:dashboard_id>/datasets/<int:version_id>/remove/", dashboard_remove_dataset, name="dashboard-remove-dataset"),

    # Dashboard interactive filters
    path("dashboards/<uuid:dashboard_id>/filters/save/", dashboard_save_filters, name="dashboard-save-filters"),
    path("dashboards/<uuid:dashboard_id>/filters/apply/", dashboard_apply_filters, name="dashboard-apply-filters"),
    path("dashboards/<uuid:dashboard_id>/filters/columns/", dashboard_get_filter_columns, name="dashboard-filter-columns"),

    # AI-powered endpoints
    path("dashboards/<uuid:dashboard_id>/widgets/<uuid:widget_id>/ai-analyze/", dashboard_ai_analyze_widget, name="dashboard-ai-analyze-widget"),
    path("dashboards/<uuid:dashboard_id>/ai/suggest-slicers/", dashboard_ai_suggest_slicers, name="dashboard-ai-suggest-slicers"),
    path("dashboards/<uuid:dashboard_id>/ai/clean-dataset/", dashboard_ai_clean_dataset, name="dashboard-ai-clean-dataset"),
    path("dashboards/<uuid:dashboard_id>/ai/executive-summary/", dashboard_ai_executive_summary, name="dashboard-ai-executive-summary"),
]
