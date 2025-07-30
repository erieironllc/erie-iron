from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.enums import TaskStatus
from erieiron_autonomous_agent.models import CodeFile, SelfDrivingTaskIteration, SelfDrivingTask, Business, Task


class Command(BaseCommand):
    def handle(self, *args, **options):
        ...
        SelfDrivingTaskIteration.objects.filter(self_driving_task__task_id="task_implement_email_processor_lambda").delete()
        CodeFile.objects.all().delete()
        # SelfDrivingTask.objects.filter(task_id="task_implement_email_processor_lambda").update(test_file_path=None)
        Task.objects.filter(id="task_implement_email_processor_lambda").update(status=TaskStatus.IN_PROGRESS)
