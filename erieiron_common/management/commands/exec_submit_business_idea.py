from pathlib import Path

from django.core.management import BaseCommand

from erieiron_autonomous_agent.board_level_agents import corporate_development_agent
from erieiron_common.enums import BusinessIdeaSource
from erieiron_common.models import BusinessAnalysis, PubSubMessage


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
        corporate_development_agent.submit_business_opportunity(
            {
                "idea_content": Path(options.get("file_name")),
                "source": BusinessIdeaSource.HUMAN
            }
        )
