from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ('workspaces', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Dataset',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='datasets', to='workspaces.workspace')),
            ],
        ),
        migrations.CreateModel(
            name='DatasetVersion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.PositiveIntegerField(default=1)),
                ('source_file', models.FileField(upload_to='datasets/%Y/%m/%d')),
                ('row_count', models.PositiveIntegerField(default=0)),
                ('column_count', models.PositiveIntegerField(default=0)),
                ('uploaded_at', models.DateTimeField(auto_now_add=True)),
                ('dataset', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='versions', to='datasets.dataset')),
            ],
        ),
        migrations.CreateModel(
            name='DatasetColumn',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('kind', models.CharField(choices=[('dimension', 'Dimension'), ('measure', 'Measure'), ('date', 'Date'), ('id', 'ID'), ('unknown', 'Unknown')], default='unknown', max_length=16)),
                ('dtype', models.CharField(default='object', max_length=64)),
                ('null_ratio', models.FloatField(default=0)),
                ('dataset_version', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='columns', to='datasets.datasetversion')),
            ],
        ),
    ]
