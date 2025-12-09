from django.core.management.base import BaseCommand

from erieiron_common.models import PubSubMessage


class Command(BaseCommand):
    def handle(self, env_type=None, *args, **options):
        PubSubMessage.objects.all().delete()
