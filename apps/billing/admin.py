from django.contrib import admin

from .models import UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "plan",
        "subscription_is_active",
        "ls_subscription_status",
        "subscription_renews_at",
        "updated_at",
    )
    list_filter = ("plan", "ls_subscription_status", "created_at")
    search_fields = ("user__username", "user__email", "ls_customer_id", "ls_subscription_id")
    autocomplete_fields = ("user",)
    readonly_fields = ("created_at", "updated_at")
