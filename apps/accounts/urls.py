from django.urls import path

from .views import AppLoginView, AppLogoutView, signup_view

urlpatterns = [
    path("login/", AppLoginView.as_view(), name="login"),
    path("logout/", AppLogoutView.as_view(), name="logout"),
    path("signup/", signup_view, name="signup"),
]
