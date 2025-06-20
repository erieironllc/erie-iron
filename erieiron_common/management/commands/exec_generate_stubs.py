from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.business_level_agents.self_driving_code.generate_stub import generate_stub


class Command(BaseCommand):
    def handle(self, *args, **options):
        generate_stub(
            "./erieiron_coder/self_driving_code/agent_tools.py",
            "./erieiron_coder/self_driving_code/agent_tools_stub.py",
        )
