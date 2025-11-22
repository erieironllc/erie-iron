from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.business_level_agents import worker_design


class Command(BaseCommand):
    def handle(self, env_type=None, *args, **options):
        worker_design.do_work("2c52e0c6-2cf9-469c-a5c0-fcb0103e856d")
