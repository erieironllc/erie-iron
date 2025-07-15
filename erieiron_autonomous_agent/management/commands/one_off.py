from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.business_level_agents.eng_lead import bootstrap_buiness
from erieiron_autonomous_agent.models import CodeFile


class Command(BaseCommand):
    def handle(self, *args, **options):
        CodeFile.objects.all().delete()
        bootstrap_buiness("09fc9301-f50f-492a-9d9d-93a3b3bf1fad")
