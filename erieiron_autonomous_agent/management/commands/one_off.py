from django.core.management.base import BaseCommand
import json

from tqdm import tqdm

from erieiron_autonomous_agent.business_level_agents.eng_lead import identify_required_credentials
from erieiron_autonomous_agent.enums import BusinessStatus
from erieiron_autonomous_agent.models import Business, InfrastructureStack, Task
from erieiron_common.llm_apis import llm_interface


class Command(BaseCommand):
    def handle(self, env_type=None, *args, **options):
        task = Task.objects.get(id="task_extend_recommendation_model_for_chat")