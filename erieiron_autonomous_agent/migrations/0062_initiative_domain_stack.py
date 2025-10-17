from django.db import migrations, models


def move_iteration_data_to_initiative(apps, schema_editor):
    Initiative = apps.get_model("erieiron_autonomous_agent", "Initiative")
    SelfDrivingTaskIteration = apps.get_model("erieiron_autonomous_agent", "SelfDrivingTaskIteration")

    for initiative in Initiative.objects.all():
        iteration = (
            SelfDrivingTaskIteration.objects
            .filter(self_driving_task__task__initiative_id=initiative.id)
            .order_by("-timestamp")
            .first()
        )

        if not iteration:
            continue

        updates = {}
        iteration_domain = getattr(iteration, "domain", None)
        if iteration_domain and not initiative.domain:
            updates["domain"] = iteration_domain

        iteration_stack_name = getattr(iteration, "cloudformation_stack_name", None)
        if iteration_stack_name and not initiative.cloudformation_stack_name:
            updates["cloudformation_stack_name"] = iteration_stack_name

        iteration_stack_id = getattr(iteration, "cloudformation_stack_id", None)
        if iteration_stack_id and not initiative.cloudformation_stack_id:
            updates["cloudformation_stack_id"] = iteration_stack_id

        if updates:
            Initiative.objects.filter(pk=initiative.pk).update(**updates)


def noop_reverse(apps, schema_editor):
    # No-op reverse migration; legacy fields are deprecated.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("erieiron_autonomous_agent", "0061_move_task_fields_to_iteration"),
    ]

    operations = [
        migrations.AddField(
            model_name="initiative",
            name="cloudformation_stack_id",
            field=models.TextField(null=True),
        ),
        migrations.AddField(
            model_name="initiative",
            name="cloudformation_stack_name",
            field=models.TextField(null=True),
        ),
        migrations.AddField(
            model_name="initiative",
            name="domain",
            field=models.TextField(null=True),
        ),
        migrations.RunPython(move_iteration_data_to_initiative, noop_reverse),
        migrations.RemoveField(
            model_name="selfdrivingtaskiteration",
            name="domain",
        ),
    ]

