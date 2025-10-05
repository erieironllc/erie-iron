from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("erieiron_autonomous_agent", "0053_selfdrivingtask_domain"),
    ]

    operations = [
        migrations.AddField(
            model_name="business",
            name="web_container_cpu",
            field=models.PositiveIntegerField(default=512),
        ),
        migrations.AddField(
            model_name="business",
            name="web_container_memory",
            field=models.PositiveIntegerField(default=1024),
        ),
        migrations.AddField(
            model_name="business",
            name="web_desired_count",
            field=models.PositiveIntegerField(default=1),
        ),
    ]
