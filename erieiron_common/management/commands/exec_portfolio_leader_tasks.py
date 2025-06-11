from django.core.management import BaseCommand

from erieiron_common.enums import PubSubMessageType
from erieiron_common.message_queue.pubsub_manager import PubSubManager


class Command(BaseCommand):
    def handle(self, *args, **options):
        PubSubManager.publish(
            PubSubMessageType.PORTFOLIO_LEADER_EXEC_REQUESTED,
        )
