from django.urls import path
from . import views

urlpatterns = [
    path("", views.admin_overview, name="admin-overview"),
    path("users/", views.admin_users, name="admin-users"),
    path("users/<int:user_id>/", views.admin_user_detail, name="admin-user-detail"),
    path("subscriptions/", views.admin_subscriptions, name="admin-subscriptions"),
    path("plans/", views.admin_plans, name="admin-plans"),
]
