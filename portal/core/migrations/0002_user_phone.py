# Generated by Django 3.2.9 on 2021-12-01 17:47

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='phone',
            field=models.CharField(max_length=15, null=True, unique=True),
        ),
    ]
