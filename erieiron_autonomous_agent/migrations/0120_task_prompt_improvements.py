import django.db.models.deletion
import erieiron_common.json_encoder
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        (
            "erieiron_autonomous_agent",
            "0119_task_implementation_versions_and_execution_audit",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="last_prompt_improvement_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="task",
            name="prompt_improvement_schedule",
            field=models.TextField(
                choices=[
                    ("NOT_APPLICABLE", "Not applicable"),
                    ("ONCE", "Once"),
                    ("DAEMON", "Daemon"),
                    ("HOURLY", "Hourly"),
                    ("DAILY", "Daily"),
                    ("WEEKLY", "Weekly"),
                ],
                default="NOT_APPLICABLE",
            ),
        ),
        migrations.CreateModel(
            name="TaskPromptImprovement",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "status",
                    models.TextField(
                        choices=[
                            ("pending_review", "Pending review"),
                            ("rejected", "Rejected"),
                            ("applied", "Applied"),
                            ("rolled_back", "Rolled back"),
                        ],
                        default="pending_review",
                    ),
                ),
                (
                    "trigger_source",
                    models.TextField(
                        choices=[
                            ("manual", "Manual"),
                            ("scheduled", "Scheduled"),
                        ],
                        default="manual",
                    ),
                ),
                ("context_json", models.JSONField(default=dict, encoder=erieiron_common.json_encoder.ErieIronJSONEncoder)),
                ("proposal_json", models.JSONField(default=dict, encoder=erieiron_common.json_encoder.ErieIronJSONEncoder)),
                ("candidate_prompt_text", models.TextField()),
                ("review_notes", models.TextField(blank=True, null=True)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("applied_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "applied_implementation_version",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="erieiron_autonomous_agent.taskimplementationversion",
                    ),
                ),
                (
                    "base_implementation_version",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="erieiron_autonomous_agent.taskimplementationversion",
                    ),
                ),
                (
                    "generated_llm_request",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="erieiron_autonomous_agent.llmrequest",
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="prompt_improvements",
                        to="erieiron_autonomous_agent.task",
                    ),
                ),
            ],
            options={
                "db_table": "erieiron_taskpromptimprovement",
                "indexes": [
                    models.Index(fields=["task", "status"], name="erieiron_au_task_id_1b8c0c_idx"),
                    models.Index(fields=["task", "created_at"], name="erieiron_au_task_id_b64fde_idx"),
                ],
            },
        ),
    ]
