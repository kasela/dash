from django.conf import settings
from django.db import models
from django.utils import timezone


class UserProfile(models.Model):
    class Plan(models.TextChoices):
        FREE = "free", "Free"
        LIGHT = "light", "Light"
        PLUS = "plus", "Plus"
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
    def is_light(self) -> bool:
        return self.plan in (self.Plan.LIGHT, self.Plan.PLUS, self.Plan.PRO, self.Plan.ENTERPRISE)

    @property
    def is_plus(self) -> bool:
        return self.plan in (self.Plan.PLUS, self.Plan.PRO, self.Plan.ENTERPRISE)

    @property
    def is_pro(self) -> bool:
        return self.plan in (self.Plan.PRO, self.Plan.ENTERPRISE)

    @property
    def max_dashboards(self) -> int:
        if self.plan == self.Plan.FREE:
            return 3
        elif self.plan == self.Plan.LIGHT:
            return 10
        elif self.plan == self.Plan.PLUS:
            return 50
        return 999_999

    @property
    def max_monthly_uploads(self) -> int:
        if self.plan == self.Plan.FREE:
            return 5
        elif self.plan == self.Plan.LIGHT:
            return 25
        elif self.plan == self.Plan.PLUS:
            return 100
        return 999_999

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
