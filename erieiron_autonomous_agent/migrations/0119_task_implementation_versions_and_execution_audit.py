import django.db.models.deletion
import erieiron_common.json_encoder
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        (
            "erieiron_autonomous_agent",
            "0118_seed_default_workflows",
        ),
    ]

    operations = [
        migrations.CreateModel(
            name="TaskImplementationVersion",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("source_kind", models.TextField(choices=[("llm_prompt", "Llm prompt"), ("code_file", "Code file")])),
                ("version_number", models.PositiveIntegerField()),
                ("application_repo_file_path", models.TextField(blank=True, null=True)),
                ("application_repo_ref", models.TextField(blank=True, null=True)),
                ("source_metadata", models.JSONField(blank=True, default=dict, encoder=erieiron_common.json_encoder.ErieIronJSONEncoder)),
                ("evaluator_config", models.JSONField(blank=True, default=dict, encoder=erieiron_common.json_encoder.ErieIronJSONEncoder)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("task", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="erieiron_autonomous_agent.task")),
            ],
            options={
                "db_table": "erieiron_taskimplementationversion",
                "unique_together": {("task", "version_number")},
                "indexes": [
                    models.Index(fields=["task", "source_kind"], name="erieiron_au_task_id_3998d8_idx"),
                    models.Index(fields=["task", "created_at"], name="erieiron_au_task_id_91d71b_idx"),
                ],
            },
        ),
        migrations.AddField(
            model_name="task",
            name="active_implementation_version",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="erieiron_autonomous_agent.taskimplementationversion"),
        ),
        migrations.AddField(
            model_name="task",
            name="implementation_source_kind",
            field=models.TextField(blank=True, choices=[("llm_prompt", "Llm prompt"), ("code_file", "Code file")], null=True),
        ),
        migrations.AddField(
            model_name="taskexecution",
            name="evaluation_metadata",
            field=models.JSONField(default=dict, encoder=erieiron_common.json_encoder.ErieIronJSONEncoder),
        ),
        migrations.AddField(
            model_name="taskexecution",
            name="evaluation_score",
            field=models.FloatField(null=True),
        ),
        migrations.AddField(
            model_name="taskexecution",
            name="implementation_provenance",
            field=models.JSONField(default=dict, encoder=erieiron_common.json_encoder.ErieIronJSONEncoder),
        ),
        migrations.AddField(
            model_name="taskexecution",
            name="implementation_source_kind",
            field=models.TextField(blank=True, choices=[("llm_prompt", "Llm prompt"), ("code_file", "Code file")], null=True),
        ),
        migrations.AddField(
            model_name="taskexecution",
            name="implementation_version",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="erieiron_autonomous_agent.taskimplementationversion"),
        ),
        migrations.AddField(
            model_name="taskexecution",
            name="model_metadata",
            field=models.JSONField(default=dict, encoder=erieiron_common.json_encoder.ErieIronJSONEncoder),
        ),
    ]
