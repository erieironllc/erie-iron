import os
import pprint
from pathlib import Path

from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.business_level_agents import eng_lead
from erieiron_autonomous_agent.business_level_agents.eng_lead import identify_required_credentials
from erieiron_autonomous_agent.models import Initiative, Business, SelfDrivingTaskIteration, InfrastructureStack, Task, CodeFile
from erieiron_common import aws_utils, common
from erieiron_common.enums import EnvironmentType, InfrastructureStackType

asdf = {
    "cef7bfac-9d17-40e8-83cd-40aa3fab6b9c": "ap_app.tf",
    "6b8eba23-68b4-4f4f-b52a-461cfad5fd06": "ap_foundation.tf",
    "b270dd96-b505-4787-9435-3627349c4463": "ei_app.tf",
    "25a5b26b-32cd-4d3e-b09f-e1a43db54a3b": "ei_foundation.tf",
}

class Command(BaseCommand):
    def handle(self, env_type=None, *args, **options):
        for stack in InfrastructureStack.objects.filter(stack_configuration__isnull=True).order_by("business__name", "stack_type"):
            task = stack.initiative.get_first_task_to_implement()
            
            contents = common.assert_exists(Path.cwd() / ".codex" / asdf[str(stack.id)]).read_text()
            
            stack.stack_configuration = contents
            stack.save()


            print(stack.id, stack.business.name, stack.stack_type)
