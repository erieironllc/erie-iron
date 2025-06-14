from django.core.management.base import BaseCommand

from erieiron_common.models import EngineeringTask, ProductInitiative


class Command(BaseCommand):
    def handle(self, *args, **options):
        # ProductInitiative.objects.all().delete()
        EngineeringTask.objects.all().delete()
