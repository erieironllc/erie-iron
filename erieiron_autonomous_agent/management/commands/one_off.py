from django.core.management.base import BaseCommand
import json

from tqdm import tqdm

from erieiron_autonomous_agent.business_level_agents.eng_lead import identify_required_credentials
from erieiron_autonomous_agent.enums import BusinessStatus
from erieiron_autonomous_agent.models import Business, InfrastructureStack
from erieiron_common.llm_apis import llm_interface


class Command(BaseCommand):
    def handle(self, env_type=None, *args, **options):
        for business in tqdm(Business.objects.filter(status=BusinessStatus.ACTIVE)):
            identify_required_credentials(business)