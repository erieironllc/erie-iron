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
        identify_required_credentials(Business.get_erie_iron_business())
