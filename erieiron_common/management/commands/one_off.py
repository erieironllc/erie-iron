from django.core.management.base import BaseCommand

from erieiron_common import common
from erieiron_common.models import EngineeringTask, ProductInitiative, Business


class Command(BaseCommand):
    def handle(self, *args, **options):
        # ProductInitiative.objects.all().delete()
        EngineeringTask.objects.all().delete()

        for b in Business.objects.all():
            b.sandbox_dir_name = common.strip_non_alpha(b.name)
            b.save()
