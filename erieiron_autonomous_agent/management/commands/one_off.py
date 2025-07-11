from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.business_level_agents.eng_lead import bootstrap_buiness


class Command(BaseCommand):
    def handle(self, *args, **options):
        bootstrap_buiness("7e482354-23eb-4398-b7f4-dd158c3797be")
