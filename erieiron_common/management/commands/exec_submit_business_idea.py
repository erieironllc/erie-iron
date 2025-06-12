from pathlib import Path

from django.core.management import BaseCommand

from erieiron_autonomous_agent.portfolio_level_agents import portfolio_leader
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
        PubSubMessage.objects.all().delete()
        BusinessAnalysis.objects.filter(business__id=options.get("business_id")).delete()

        portfolio_leader.submit_business_idea(
            Path(options.get("file_name")),
            options.get("business_id"),
            source=BusinessIdeaSource.HUMAN
        )
