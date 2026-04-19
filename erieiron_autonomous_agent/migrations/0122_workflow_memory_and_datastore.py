from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("erieiron_autonomous_agent", "0121_rename_erieiron_au_task_id_3998d8_idx_erieiron_ta_task_id_22da9c_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="workflowdefinition",
            name="datastore_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="workflowdefinition",
            name="long_term_memory_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="workflowdefinition",
            name="datastore_backend",
            field=models.TextField(
                choices=[
                    ("SQLITE", "SQLite"),
                    ("POSTGRES", "Postgres"),
                    ("MONGO", "Mongo"),
                    ("DYNAMODB", "DynamoDB"),
                ],
                default="SQLITE",
            ),
        ),
    ]
