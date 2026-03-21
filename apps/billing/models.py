from django.conf import settings
from django.db import models
from django.utils import timezone


class UserProfile(models.Model):
    class Plan(models.TextChoices):
        FREE = "free", "Free"
        PRO = "pro", "Pro"
        ENTERPRISE = "enterprise", "Enterprise"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    plan = models.CharField(max_length=20, choices=Plan.choices, default=Plan.FREE)

    # LemonSqueezy fields
    ls_customer_id = models.CharField(max_length=100, blank=True)
    ls_subscription_id = models.CharField(max_length=100, blank=True)
    ls_subscription_status = models.CharField(max_length=50, blank=True)  # active, cancelled, paused, past_due
    ls_variant_id = models.CharField(max_length=100, blank=True)
    subscription_ends_at = models.DateTimeField(null=True, blank=True)
    subscription_renews_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.user.username} ({self.plan})"

    # ── Plan limits ────────────────────────────────────────────────────────────

    @property
    def is_pro(self) -> bool:
        return self.plan in (self.Plan.PRO, self.Plan.ENTERPRISE)

    @property
    def max_dashboards(self) -> int:
        return 999_999 if self.is_pro else 3

    @property
    def max_monthly_uploads(self) -> int:
        return 999_999 if self.is_pro else 5

    @property
    def subscription_is_active(self) -> bool:
        if self.plan == self.Plan.FREE:
            return True
        if self.ls_subscription_status == "active":
            return True
        # grace period – still active until period ends
        if self.ls_subscription_status in ("cancelled", "past_due") and self.subscription_ends_at:
            return self.subscription_ends_at > timezone.now()
        return False

    @property
    def plan_display(self) -> str:
        return self.get_plan_display()
