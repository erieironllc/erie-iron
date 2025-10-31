import pprint
from pathlib import Path

from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.business_level_agents import eng_lead
from erieiron_autonomous_agent.models import Initiative, Business, SelfDrivingTaskIteration, InfrastructureStack
from erieiron_common import aws_utils, common
from erieiron_common.enums import EnvironmentType


class Command(BaseCommand):
    def handle(self, env_type=None, *args, **options):
        stacks:list[InfrastructureStack] = list(InfrastructureStack.objects.all())
        for stack in stacks:
            stack.tombstone(Path("/var/folders/fm/58h_5c2978z6qtvscgt0lpkm0000gn/T/tmpre_a9kkh"))
