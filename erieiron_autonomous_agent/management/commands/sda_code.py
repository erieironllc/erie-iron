from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.coding_agents import self_driving_coder_agent


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument(
            '--task_id',
            type=str,
            required=False
        )
    
    def handle(self, *args, **options):
        self_driving_coder_agent.execute(
            options.get("task_id")
        )
