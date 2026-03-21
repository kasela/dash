from django.contrib import admin
from django.urls import path

from apps.dashboards.views import dashboard_home
from apps.datasets.views import dataset_upload, dataset_upload_result

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", dashboard_home, name="dashboard-home"),
    path("datasets/upload/", dataset_upload, name="dataset-upload"),
    path("datasets/upload/result/", dataset_upload_result, name="dataset-upload-result"),
]
