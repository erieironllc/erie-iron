from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("erieiron_autonomous_agent", "0059_remove_task_test_plan"),
    ]

    operations = [
        migrations.AddField(
            model_name="business",
            name="route53_hosted_zone_id",
            field=models.TextField(blank=True, null=True),
        ),
    ]
