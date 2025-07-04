# erieiron_common/apps.py
from django.apps import AppConfig


class ErieironAutonomousAgentConfig(AppConfig):
    name = 'erieiron_autonomous_agent'

    def ready(self):
        import erieiron_autonomous_agent.models
