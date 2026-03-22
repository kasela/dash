from django.urls import path

from .views import AppLoginView, AppLogoutView, signup_view, api_key_create, api_key_revoke

urlpatterns = [
    path("login/", AppLoginView.as_view(), name="login"),
    path("logout/", AppLogoutView.as_view(), name="logout"),
    path("signup/", signup_view, name="signup"),
    path("api-keys/create/", api_key_create, name="api-key-create"),
    path("api-keys/<int:key_id>/revoke/", api_key_revoke, name="api-key-revoke"),
]
