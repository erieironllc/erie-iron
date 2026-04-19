from django.db import migrations, models


APPLICATION_MANAGED_WORKFLOW_NAMES = (
    "Board Workflow",
    "Business Workflow",
)


def mark_seeded_workflows_application_repo(apps, schema_editor):
    WorkflowDefinition = apps.get_model("erieiron_autonomous_agent", "WorkflowDefinition")
    WorkflowDefinition.objects.filter(
        name__in=APPLICATION_MANAGED_WORKFLOW_NAMES,
    ).update(source_kind="application_repo")


def mark_seeded_workflows_internal(apps, schema_editor):
    WorkflowDefinition = apps.get_model("erieiron_autonomous_agent", "WorkflowDefinition")
    WorkflowDefinition.objects.filter(
        name__in=APPLICATION_MANAGED_WORKFLOW_NAMES,
    ).update(source_kind="erie_iron_internal")


class Migration(migrations.Migration):
    dependencies = [
        ("erieiron_autonomous_agent", "0121_rename_erieiron_au_task_id_3998d8_idx_erieiron_ta_task_id_22da9c_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="workflowdefinition",
            name="source_kind",
            field=models.TextField(
                choices=[
                    ("application_repo", "Application repo"),
                    ("erie_iron_internal", "Erie iron internal"),
                ],
                default="application_repo",
                help_text="Whether the workflow comes from the application repo or Erie Iron runtime internals.",
            ),
        ),
        migrations.RunPython(
            mark_seeded_workflows_application_repo,
            reverse_code=mark_seeded_workflows_internal,
        ),
    ]
