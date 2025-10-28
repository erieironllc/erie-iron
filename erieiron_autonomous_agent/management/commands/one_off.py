import pprint

from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.business_level_agents import eng_lead
from erieiron_autonomous_agent.models import Initiative, Business
from erieiron_common import aws_utils, common
from erieiron_common.enums import AwsEnv


class Command(BaseCommand):
    def handle(self, aws_env=None, *args, **options):
        initiative = Initiative.objects.get(id="articleparser_usagelimit_ads_sep2025_token")
        initiative.tasks.all().delete()
        eng_lead.define_tasks_for_initiative(initiative.id)
