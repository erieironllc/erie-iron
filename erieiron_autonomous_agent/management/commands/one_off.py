from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.business_level_agents import worker_design


class Command(BaseCommand):
    def handle(self, env_type=None, *args, **options):
        worker_design.do_work("3be0c2a5-e179-4800-af2c-0feb5c99fff5")
