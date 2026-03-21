from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ('datasets', '0001_initial'),
        ('workspaces', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Dashboard',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=200)),
                ('is_public', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('dataset_version', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='dashboards', to='datasets.datasetversion')),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='dashboards', to='workspaces.workspace')),
            ],
        ),
        migrations.CreateModel(
            name='DashboardWidget',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=200)),
                ('widget_type', models.CharField(choices=[('kpi', 'KPI'), ('bar', 'Bar'), ('line', 'Line'), ('pie', 'Pie'), ('table', 'Table')], max_length=16)),
                ('position', models.PositiveIntegerField(default=0)),
                ('chart_config', models.JSONField(default=dict)),
                ('dashboard', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='widgets', to='dashboards.dashboard')),
            ],
        ),
    ]
