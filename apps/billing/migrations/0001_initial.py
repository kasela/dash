from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UserProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("plan", models.CharField(choices=[("free", "Free"), ("pro", "Pro"), ("enterprise", "Enterprise")], default="free", max_length=20)),
                ("ls_customer_id", models.CharField(blank=True, max_length=100)),
                ("ls_subscription_id", models.CharField(blank=True, max_length=100)),
                ("ls_subscription_status", models.CharField(blank=True, max_length=50)),
                ("ls_variant_id", models.CharField(blank=True, max_length=100)),
                ("subscription_ends_at", models.DateTimeField(blank=True, null=True)),
                ("subscription_renews_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="profile",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
    ]
