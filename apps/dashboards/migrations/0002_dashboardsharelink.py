from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [("dashboards", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="DashboardShareLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("dashboard", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="share_links", to="dashboards.dashboard")),
            ],
        ),
    ]
