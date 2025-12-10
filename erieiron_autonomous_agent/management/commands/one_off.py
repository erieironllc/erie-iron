from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.business_level_agents.worker_design import create_business_design_spec
from erieiron_autonomous_agent.models import Business
from erieiron_common.models import PubSubMessage


class Command(BaseCommand):
    def handle(self, env_type=None, *args, **options):
        create_business_design_spec("440e8634-5e51-4594-930b-19bec225ee65")
