from django.core.management.base import BaseCommand
import json

from tqdm import tqdm

from erieiron_autonomous_agent.business_level_agents.eng_lead import identify_required_credentials
from erieiron_autonomous_agent.enums import BusinessStatus
from erieiron_autonomous_agent.models import Business, InfrastructureStack, Task, CodeFile
from erieiron_common.llm_apis import llm_interface


class Command(BaseCommand):
    def handle(self, env_type=None, *args, **options):
        CodeFile.objects.filter(
            business__id="2c52e0c6-2cf9-469c-a5c0-fcb0103e856d",
            file_path__contains="test_task_bug_report_articleparsernew_t57y4lei"
        ).delete()
        # task = Task.objects.get(id="task_extend_recommendation_model_for_chat")