from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.coding_agents import self_driving_coder_agent


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument(
            '--task_id',
            type=str,
            required=True
        )
        
        parser.add_argument(
            '--reset',
            type=bool,
            required=False
        )
        
        parser.add_argument(
            '--code_now',
            type=bool,
            required=False
        )
        
        parser.add_argument(
            '--plan_now',
            type=bool,
            required=False
        )
    
    def handle(self, *args, **options):
        self_driving_coder_agent.execute(
            options.get("task_id"),
            options.get("reset"),
            options.get("code_now"),
            options.get("plan_now")
        )
