from django.core.management.base import BaseCommand

from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.business_level_agents.eng_lead import INITIATIVE_TITLE_BOOTSTRAP_ENVS
from erieiron_autonomous_agent.models import CodeFile, SelfDrivingTaskIteration, Business, Task, SelfDrivingTask


class Command(BaseCommand):
    def handle(self, *args, **options):
        ...
        SelfDrivingTaskIteration.objects.all().delete()
        CodeFile.objects.all().delete()
