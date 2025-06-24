from erieiron_common import common
from erieiron_common.models import Task, SelfDrivingTask


def do_work(task_id):
    task = Task.objects.get(id=task_id)

    print(f"python manage.py sda_code --task_id={task_id}")

    # common.execute_management_cmd(
    #     f"sda_code --config={config_file}",
    #     log_file
    # )
