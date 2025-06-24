from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.business_level_agents.self_driving_coder.self_driving_coder_config import SelfDriverConfig
from erieiron_common import common
from erieiron_common.models import SelfDrivingTaskIteration


class Command(BaseCommand):

    def add_arguments(self, parser):
        parser.add_argument(
            '--iteration_id',
            type=str,
            required=False
        )

    def handle(self, *args, **options):
        sd_iteration = SelfDrivingTaskIteration.objects.get(id=options.get("iteration_id"))
        sd_iteration.write_to_disk()

        self_driving_task = sd_iteration.task
        config = SelfDriverConfig.get(
            self_driving_task.config_path,
            self_driving_task.related_task_id
        )

        test_exit_code = common.exec_cmd(
            f"pytest -v {config.main_code_file_test.get_path()}"
        )

        if test_exit_code == 0:
            python_file = str(config.main_code_file.get_path())
            print(f"executing {python_file}")
            code_module = common.import_module_from_path(python_file)
            code_module.execute()
