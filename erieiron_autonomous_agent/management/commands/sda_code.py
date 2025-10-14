from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.coding_agents import self_driving_coder_agent
from erieiron_autonomous_agent.coding_agents.self_driving_coder_config import SdaInitialAction


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument(
            '--task_id',
            type=str,
            required=True
        )
        
        parser.add_argument(
            '--action',
            type=str,
            choices=[a.name for a in SdaInitialAction],
            required=False
        )
    
    def handle(self, *args, **options):
        self_driving_coder_agent.execute(
            options.get("task_id"),
            SdaInitialAction.valid_or(options.get("action"))
        )
