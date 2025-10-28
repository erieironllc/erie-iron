import pprint

from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.business_level_agents import eng_lead
from erieiron_autonomous_agent.models import Initiative, Business, SelfDrivingTaskIteration
from erieiron_common import aws_utils, common
from erieiron_common.enums import AwsEnv


class Command(BaseCommand):
    def handle(self, aws_env=None, *args, **options):
        for iteration in SelfDrivingTaskIteration.objects.filter(evaluation_json__isnull=False).order_by("-timestamp"):
            if iteration.evaluation_json.get("test_errors"):
                pprint.pprint(iteration.evaluation_json.get("test_errors"))
                return
