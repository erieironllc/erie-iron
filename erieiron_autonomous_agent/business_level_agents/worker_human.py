from erieiron_common import aws_utils, settings_common
from erieiron_autonomous_agent.models import Task


def do_work(task_id):
    task = Task.objects.get(id=task_id)
    if not task.allow_execution():
        return

    aws_utils.get_aws_interface().send_email(
        subject=f"[ErieIron] Human action required for Task {task.id}",
        recipient="erieironllc@gmail.com",
        body=f"""
A task requires your input
{settings_common.BASE_URL}/task/{task.id}

{task.description}

{task.completion_criteria}
"""
    )
