from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.enums import TaskStatus
from erieiron_autonomous_agent.models import CodeFile, SelfDrivingTaskIteration, SelfDrivingTask, Business, Task


class Command(BaseCommand):
    def handle(self, *args, **options):
        SelfDrivingTaskIteration.objects.filter(self_driving_task__task_id="task_implement_digest_worker_lambda").delete()

