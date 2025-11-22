import shutil

from django.core.management.base import BaseCommand

import settings
from erieiron_autonomous_agent.board_level_agents import board_analyst
from erieiron_autonomous_agent.business_level_agents import eng_lead
from erieiron_autonomous_agent.business_level_agents.eng_lead import bootstrap_buiness
from erieiron_autonomous_agent.coding_agents import coding_agent
from erieiron_autonomous_agent.enums import TaskStatus
from erieiron_autonomous_agent.models import CodeFile, SelfDrivingTask, SelfDrivingTaskIteration, SelfDrivingTaskBestIteration, Task, Business
from erieiron_common.enums import PubSubMessageType
from erieiron_common.message_queue.pubsub_manager import PubSubManager
from erieiron_common.models import PubSubMessage


class Command(BaseCommand):
    def handle(self, *args, **options):
        business = Business.objects.get(id="3be0c2a5-e179-4800-af2c-0feb5c99fff5")
        # business_analysis = board_analyst.execute_business_analysis(business)
        business_analysis = board_analyst.define_human_job_descriptions(business.id)
