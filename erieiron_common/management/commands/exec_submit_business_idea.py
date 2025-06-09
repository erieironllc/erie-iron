from pathlib import Path

from django.core.management import BaseCommand

from erieiron_autonomous_agent import system_agent, agent_api
from erieiron_common.enums import SystemAgentTask


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument(
            '--file_name',
            type=str,
            required=True
        )

        parser.add_argument(
            '--business_id',
            type=str,
            required=False
        )

    def handle(self, *args, **options):
        agent_api.submit_business_idea(
            Path(options.get("file_name")),
            options.get("business_id")
        )
