from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.business_level_agents import eng_lead
from erieiron_autonomous_agent.coding_agents.self_driving_coder_agent import on_reset_task_test
from erieiron_autonomous_agent.models import Task, SelfDrivingTask, SelfDrivingTaskIteration, CodeFile
from erieiron_common import common


class Command(BaseCommand):
    def handle(self, *args, **options):
        self.reset_task_and_business("task_implement_llm_schema_and_utils")
    
    def reset_task_and_business(self, task_id):
        task = Task.objects.get(id=task_id)
        try:
            common.delete_dir(task.selfdrivingtask.sandbox_path)
        except:
            ...
        SelfDrivingTaskIteration.objects.filter(self_driving_task__task_id=task_id).delete()
        CodeFile.objects.filter(business_id=task.initiative.business_id).delete()
        eng_lead.bootstrap_buiness(task.initiative.business_id)

