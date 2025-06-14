from erieiron_common.models import Business, Task


def do_work(task_id):
    task = Task.objects.get(id=task_id)
    sandbox = task.product_initiative.business.get_sandbox_dir()
    print("SANDBOX", sandbox)
