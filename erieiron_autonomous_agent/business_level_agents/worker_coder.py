from erieiron_autonomous_agent.models import Task
from erieiron_common import common


def do_work(task_id):
    task = Task.objects.get(id=task_id)
    
    print(f"python manage.py sda_code --task_id={task_id}")
    
    # common.execute_management_cmd(
    #     f"sda_code --task_id={task_id}"
    # )
