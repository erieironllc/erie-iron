from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.business_level_agents.self_driving_coder.generate_stub import generate_stub
from erieiron_common.enums import TaskStatus, PubSubMessageType
from erieiron_common.message_queue.pubsub_manager import PubSubManager
from erieiron_common.models import Task


class Command(BaseCommand):
    def handle(self, *args, **options):
        for t in Task.objects.filter(status__in=[TaskStatus.NOT_STARTED, TaskStatus.BLOCKED]):
            PubSubManager.publish_id(
                PubSubMessageType.TASK_UPDATED,
                t.id
            )
