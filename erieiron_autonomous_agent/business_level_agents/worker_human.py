from erieiron_common.models import Task


def do_work(task_id):
    task = Task.objects.get(id=task_id)
    print("email human re: {task}")
    print("OR! do we create a github ticket?")
