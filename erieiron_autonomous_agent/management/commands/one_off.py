import pprint
from pathlib import Path

from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.business_level_agents import eng_lead
from erieiron_autonomous_agent.business_level_agents.eng_lead import identify_required_credentials
from erieiron_autonomous_agent.models import Initiative, Business, SelfDrivingTaskIteration, InfrastructureStack
from erieiron_common import aws_utils, common
from erieiron_common.enums import EnvironmentType


class Command(BaseCommand):
    def handle(self, env_type=None, *args, **options):
        Business.objects.filter(id="79dabfb0-35d8-4859-8ce2-695d2f94c443").delete()
        Business.objects.filter(id="97c2b33a-4ff5-4bd7-a2f2-e829db0fe3cc").update(name="Erie Iron, LLC")
