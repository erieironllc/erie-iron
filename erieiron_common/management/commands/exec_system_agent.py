from django.core.management import BaseCommand

from erieiron_autonomous_agent import system_agent
from erieiron_common.enums import SystemAgentTask


class Command(BaseCommand):

    def add_arguments(self, parser):
        parser.add_argument(
            '--task',
            type=str,
            required=False
        )

        parser.add_argument(
            '--arg',
            type=str,
            required=False
        )

    def handle(self, *args, **options):
        system_agent.execute(
            SystemAgentTask.valid_or(options.get("task"), SystemAgentTask.REVIEW_BUSINESSES),
            options.get("arg")
        )
