from erieiron_common import aws_utils
from erieiron_common.models import Task


def do_work(task_id):
    task = Task.objects.get(id=task_id)
    subject = f"[ErieIron] Human action required for Task {task.id}"
    message = f"A task requires your input:\n\nTask ID: {task.id}\nSummary: {task.description}\nSummary: {task.completion_criteria}\n\nPlease log in to take action."

    aws_utils.get_aws_interface().send_email(
        subject=subject,
        recipient="jj@jjschultz.com",
        body=message
    )
