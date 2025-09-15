from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.business_level_agents import eng_lead, domain_manager
from erieiron_autonomous_agent.coding_agents.self_driving_coder_agent import on_reset_task_test
from erieiron_autonomous_agent.models import Task, SelfDrivingTask, SelfDrivingTaskIteration, CodeFile, Business
from erieiron_common import common


class Command(BaseCommand):
    def handle(self, *args, **options):
        business = Business.objects.get(id="09fc9301-f50f-492a-9d9d-93a3b3bf1fad")
        domain_manager.manage_domain(business)

