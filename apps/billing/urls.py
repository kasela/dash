from django.urls import path
from . import views

urlpatterns = [
    path("checkout/pro/", views.checkout_redirect, name="checkout-pro"),
    path("webhook/lemonsqueezy/", views.lemonsqueezy_webhook, name="lemonsqueezy-webhook"),
    path("settings/account/", views.account_settings, name="account-settings"),
    path("settings/team/", views.team_settings, name="team-settings"),
]
