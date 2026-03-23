from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="userprofile",
            name="plan",
            field=models.CharField(
                choices=[
                    ("free", "Free"),
                    ("light", "Light"),
                    ("plus", "Plus"),
                    ("pro", "Pro"),
                    ("enterprise", "Enterprise"),
                ],
                default="free",
                max_length=20,
            ),
        ),
    ]
