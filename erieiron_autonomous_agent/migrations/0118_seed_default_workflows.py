from django.db import migrations

from erieiron_autonomous_agent.workflow_defaults import (
    remove_default_workflows,
    sync_default_workflows,
)


def seed_default_workflows(apps, schema_editor):
    sync_default_workflows(apps)


def unseed_default_workflows(apps, schema_editor):
    remove_default_workflows(apps)


class Migration(migrations.Migration):
    dependencies = [
        (
            "erieiron_autonomous_agent",
            "0117_remove_workflowstep_uniq_workflow_step_handler",
        ),
    ]

    operations = [
        migrations.RunPython(seed_default_workflows, unseed_default_workflows),
    ]
