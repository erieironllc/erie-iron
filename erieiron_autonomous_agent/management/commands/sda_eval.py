from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.models import SelfDrivingTaskIteration
from erieiron_autonomous_agent.self_driving_coder import self_driving_coder_agent


class Command(BaseCommand):

    def add_arguments(self, parser):
        parser.add_argument(
            '--iteration_id',
            type=str,
            required=False
        )

    def handle(self, *args, **options):
        sd_iteration = SelfDrivingTaskIteration.objects.get(id=options.get("iteration_id"))
        self_driving_task = sd_iteration.self_driving_task

        self_driving_coder_agent.execute_eval(
            config_file=self_driving_task.config_path,
            task_id=self_driving_task.task_id
        )
