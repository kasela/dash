from django.contrib import admin
from django.urls import include, path

from apps.dashboards.views import (
    dashboard_create_from_version,
    dashboard_create_share_link,
    dashboard_home,
    dashboard_public_view,
)
from apps.datasets.views import dataset_upload, dataset_upload_result

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("", dashboard_home, name="dashboard-home"),
    path("dashboards/create/<int:version_id>/", dashboard_create_from_version, name="dashboard-create-from-version"),
    path("dashboards/<int:dashboard_id>/share/", dashboard_create_share_link, name="dashboard-create-share"),
    path("dashboards/share/<uuid:token>/", dashboard_public_view, name="dashboard-public-view"),
    path("datasets/upload/", dataset_upload, name="dataset-upload"),
    path("datasets/upload/result/", dataset_upload_result, name="dataset-upload-result"),
]
