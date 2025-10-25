import pprint

from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.business_level_agents import eng_lead
from erieiron_autonomous_agent.models import Initiative, Business
from erieiron_common import aws_utils, common
from erieiron_common.enums import AwsEnv


class Command(BaseCommand):
    def handle(self, aws_env=None, *args, **options):
        Business.objects.filter(id="5d4d8d5a-9300-4409-8e28-ef465ef3ea35").delete()
        return
        business = Business.objects.get(id="09fc9301-f50f-492a-9d9d-93a3b3bf1fad")
        initiative = Initiative.objects.get(id="articleparser_forwarddigest_launch_token")
        business.codefile_set.all().delete()
        # eng_lead.write_business_architecture(business)
        # eng_lead.write_initiative_architecture(initiative)
        initiative.tasks.all().delete()
        eng_lead.define_tasks_for_initiative(initiative.id)
