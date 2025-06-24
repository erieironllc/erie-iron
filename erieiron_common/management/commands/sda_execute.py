from django.core.management.base import BaseCommand

from erieiron_common.models import SelfDrivingTaskIteration


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument(
            '--iteration_id',
            type=str,
            required=False
        )

    def handle(self, *args, **options):
        SelfDrivingTaskIteration.objects.get(
            id=options.get("iteration_id")
        ).execute()
