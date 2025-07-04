from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.enums import TaskStatus
from erieiron_autonomous_agent.models import Task
from erieiron_common.enums import PubSubMessageType
from erieiron_common.message_queue.pubsub_manager import PubSubManager


class Command(BaseCommand):
    def handle(self, *args, **options):
        for t in Task.objects.filter(status__in=[TaskStatus.NOT_STARTED, TaskStatus.BLOCKED]):
            PubSubManager.publish_id(
                PubSubMessageType.TASK_UPDATED,
                t.id
            )
