import shutil

from django.core.management.base import BaseCommand

import settings
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
        for business in Business.get_portfolio_business():
            PubSubManager.publish(
                PubSubMessageType.BUSINESS_JOB_DESCRIPTIONS_REQUESTED,
                payload=business.id
            )
