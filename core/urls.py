from django.contrib import admin
from django.urls import include, path

from apps.dashboards.views import (
    app_home,
    dashboard_add_widget,
    dashboard_create_from_version,
    dashboard_create_share_link,
    dashboard_delete_widget,
    dashboard_detail,
    dashboard_get_columns,
    dashboard_public_view,
    landing_page,
    pricing_page,
)
from apps.datasets.views import dataset_upload, dataset_upload_result

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),

    # Marketing pages
    path("", landing_page, name="landing"),
    path("pricing/", pricing_page, name="pricing"),

    # Authenticated app
    path("app/", app_home, name="app-home"),
    path("app/dashboards/<int:dashboard_id>/", dashboard_detail, name="dashboard-detail"),

    # Dashboard actions
    path("dashboards/create/<int:version_id>/", dashboard_create_from_version, name="dashboard-create-from-version"),
    path("dashboards/<int:dashboard_id>/share/", dashboard_create_share_link, name="dashboard-create-share"),
    path("dashboards/share/<uuid:token>/", dashboard_public_view, name="dashboard-public-view"),
    path("dashboards/<int:dashboard_id>/columns/", dashboard_get_columns, name="dashboard-get-columns"),
    path("dashboards/<int:dashboard_id>/widgets/add/", dashboard_add_widget, name="dashboard-add-widget"),
    path("dashboards/<int:dashboard_id>/widgets/<int:widget_id>/delete/", dashboard_delete_widget, name="dashboard-delete-widget"),

    # Dataset actions
    path("datasets/upload/", dataset_upload, name="dataset-upload"),
    path("datasets/upload/result/", dataset_upload_result, name="dataset-upload-result"),
]
