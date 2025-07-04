from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.self_driving_coder.generate_stub import generate_stub


class Command(BaseCommand):
    def handle(self, *args, **options):
        generate_stub(
            "./erieiron_autonomous_agent/business_level_agents/self_driving_coder/agent_tools.py",
            "./erieiron_autonomous_agent/business_level_agents/self_driving_coder/agent_tools_stub.py"
        )
