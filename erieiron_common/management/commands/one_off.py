from django.core.management.base import BaseCommand

from erieiron_common.models import EngineeringTask, Capability


class Command(BaseCommand):
    def handle(self, *args, **options):
        EngineeringTask.objects.all().delete()
        Capability.objects.all().delete()
