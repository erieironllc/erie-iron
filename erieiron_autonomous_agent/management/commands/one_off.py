import boto3
from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.business_level_agents import eng_lead
from erieiron_autonomous_agent.coding_agents.self_driving_coder_agent import on_reset_task_test
from erieiron_autonomous_agent.models import Task, SelfDrivingTask, SelfDrivingTaskIteration, Business, CodeFile
from erieiron_common import common


class Command(BaseCommand):
    def handle(self, *args, **options):
        self.reset_task_and_business("task_setup_core_infra_rds")
    
    def reset_task_and_business(self, task_id):
        task = Task.objects.get(id=task_id)
        SelfDrivingTaskIteration.objects.filter(self_driving_task__task_id=task_id).delete()
        CodeFile.objects.filter(business_id=task.initiative.business_id).delete()
        eng_lead.bootstrap_buiness(task.initiative.business_id)
        on_reset_task_test(task_id)
    
    def handle_as(self, *args, **options):
        Task.objects.filter(initiative_id="articleparser_forwarddigest_launch_token").delete()
        
        # eng_lead.bootstrap_buiness("09fc9301-f50f-492a-9d9d-93a3b3bf1fad")
        # eng_lead.on_product_initiatives_defined("09fc9301-f50f-492a-9d9d-93a3b3bf1fad")
        eng_lead.define_tasks_for_initiative(
            "articleparser_forwarddigest_launch_token",
            {}
        )

