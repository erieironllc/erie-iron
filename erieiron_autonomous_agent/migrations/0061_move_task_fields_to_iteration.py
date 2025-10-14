from django.db import migrations, models


def forward(apps, schema_editor):
    SelfDrivingTask = apps.get_model('erieiron_autonomous_agent', 'SelfDrivingTask')
    SelfDrivingTaskIteration = apps.get_model('erieiron_autonomous_agent', 'SelfDrivingTaskIteration')

    for task in SelfDrivingTask.objects.all():
        domain = getattr(task, 'domain', None)
        stack_name = getattr(task, 'cloudformation_stack_name', None)
        stack_id = getattr(task, 'cloudformation_stack_id', None)

        if not any([domain, stack_name, stack_id]):
            continue

        iterations = list(
            SelfDrivingTaskIteration.objects.filter(self_driving_task=task).order_by('timestamp')
        )

        if not iterations:
            SelfDrivingTaskIteration.objects.create(
                self_driving_task=task,
                version_number=1,
                planning_model="",
                coding_model="",
                domain=domain,
                cloudformation_stack_name=stack_name,
                cloudformation_stack_id=stack_id,
            )
            continue

        for iteration in iterations:
            update_fields = []
            if domain and not getattr(iteration, 'domain', None):
                iteration.domain = domain
                update_fields.append('domain')
            if stack_name and not getattr(iteration, 'cloudformation_stack_name', None):
                iteration.cloudformation_stack_name = stack_name
                update_fields.append('cloudformation_stack_name')
            if stack_id and not getattr(iteration, 'cloudformation_stack_id', None):
                iteration.cloudformation_stack_id = stack_id
                update_fields.append('cloudformation_stack_id')
            if update_fields:
                iteration.save(update_fields=update_fields)


def reverse(apps, schema_editor):
    SelfDrivingTask = apps.get_model('erieiron_autonomous_agent', 'SelfDrivingTask')
    SelfDrivingTaskIteration = apps.get_model('erieiron_autonomous_agent', 'SelfDrivingTaskIteration')

    for task in SelfDrivingTask.objects.all():
        iteration = (
            SelfDrivingTaskIteration.objects
            .filter(self_driving_task=task)
            .order_by('-timestamp')
            .first()
        )
        if not iteration:
            continue

        update_kwargs = {}
        if getattr(iteration, 'domain', None):
            update_kwargs['domain'] = iteration.domain
        if getattr(iteration, 'cloudformation_stack_name', None):
            update_kwargs['cloudformation_stack_name'] = iteration.cloudformation_stack_name
        if getattr(iteration, 'cloudformation_stack_id', None):
            update_kwargs['cloudformation_stack_id'] = iteration.cloudformation_stack_id
        if update_kwargs:
            SelfDrivingTask.objects.filter(pk=task.pk).update(**update_kwargs)


class Migration(migrations.Migration):

    dependencies = [
        ('erieiron_autonomous_agent', '0060_business_route53_hosted_zone_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='selfdrivingtaskiteration',
            name='domain',
            field=models.TextField(null=True),
        ),
        migrations.AddField(
            model_name='selfdrivingtaskiteration',
            name='cloudformation_stack_name',
            field=models.TextField(null=True),
        ),
        migrations.AddField(
            model_name='selfdrivingtaskiteration',
            name='cloudformation_stack_id',
            field=models.TextField(null=True),
        ),
        migrations.RunPython(forward, reverse),
        migrations.RemoveField(
            model_name='selfdrivingtask',
            name='domain',
        ),
        migrations.RemoveField(
            model_name='selfdrivingtask',
            name='cloudformation_stack_name',
        ),
        migrations.RemoveField(
            model_name='selfdrivingtask',
            name='cloudformation_stack_id',
        ),
    ]
