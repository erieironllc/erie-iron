import os
import pprint
from pathlib import Path

from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.business_level_agents import eng_lead
from erieiron_autonomous_agent.business_level_agents.eng_lead import identify_required_credentials
from erieiron_autonomous_agent.models import Initiative, Business, SelfDrivingTaskIteration, InfrastructureStack, Task, CodeFile, CloudAccount
from erieiron_common import aws_utils, common
from erieiron_common.aws_utils import AwsInterface
from erieiron_common.enums import EnvironmentType, InfrastructureStackType


class Command(BaseCommand):
    def handle(self, env_type=None, *args, **options):
        aws_interface = AwsInterface(
            CloudAccount.objects.get(id="9b53fb9a-f0bc-4db0-bc57-60e26dc22b6c")
        )
        
        if aws_interface.get_shared_vpc() is None :
            raise Exception('shared vpc is none')
        
        pprint.pprint(aws_interface.get_shared_vpc())
