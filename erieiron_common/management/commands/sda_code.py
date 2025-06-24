from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.business_level_agents.self_driving_coder import self_driving_coder_agent


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument(
            '--config',
            type=str,
            required=False
        )

        parser.add_argument(
            '--task_id',
            type=str,
            required=False
        )

    def handle(self, *args, **options):
        self_driving_coder_agent.execute(
            config_file=options.get("config"),
            task_id=options.get("task_id")
        )
