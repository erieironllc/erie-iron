from django.core.management import BaseCommand

from erieiron_common.enums import PubSubMessageType
from erieiron_common.message_queue.pubsub_manager import PubSubManager


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument(
            '--msg_type',
            type=str,
            required=True
        )

        parser.add_argument(
            '--business_id',
            type=str,
            required=True
        )

    def handle(self, *args, **options):
        PubSubManager.publish_id(
            PubSubMessageType(options.get("msg_type")),
            options.get("business_id")
        )
