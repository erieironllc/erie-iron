from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.coding_agents import self_driving_coder_runner


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument(
            '--execution_id',
            type=str,
            required=False
        )

    def handle(self, *args, **options):
        self_driving_coder_runner.execute(
            options.get("execution_id")
        )
