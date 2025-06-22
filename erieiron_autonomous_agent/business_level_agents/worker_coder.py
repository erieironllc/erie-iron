from erieiron_common import common
from erieiron_common.models import Task, SelfDrivingTask


def do_work(task_id):
    task = Task.objects.get(id=task_id)
    sandbox = task.product_initiative.business.get_sandbox_dir()

    file_name = common.strip_non_alpha(task_id, "_")
    log_file = sandbox / f"{file_name}.output.log"
    config_file = sandbox / f"{file_name}.config.json"

    config = common.get_dict(task)
    config["task_id"] = task_id
    config["generate_single_file"] = True
    config["main_file"] = sandbox / f"{file_name}.py"
    config["log_file"] = log_file
    config["max_budget_usd"] = 100

    sdt = SelfDrivingTask.get_or_create(
        related_task_id=task_id,
        config_file=config_file
    )

    config["selfdriving_task_id"] = sdt.id
    common.write_json(config_file, config)

    print(f"python manage.py exec_self_driver --config={config_file}")

    # common.execute_management_cmd(
    #     f"exec_self_driver --config={config_file}",
    #     log_file
    # )
