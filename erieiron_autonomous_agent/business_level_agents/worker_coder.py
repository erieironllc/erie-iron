from erieiron_common import common
from erieiron_common.models import Task


def do_work(task_id):
    task = Task.objects.get(id=task_id)
    sandbox = task.product_initiative.business.get_sandbox_dir()

    file_name = common.strip_non_alpha(task_id, "_")
    log_file = sandbox / f"{file_name}.output.log"
    config_file = sandbox / f"{file_name}.config.json"

    config = common.get_dict(task)
    config["task_id"] = task_id
    config["main_file"] = sandbox / f"{file_name}.py"
    config["log_file"] = log_file
    config["max_budget_usd"] = 100

    common.write_json(config_file, config)

    common.execute_management_cmd(
        f"exec_self_driver --config={config_file}",
        log_file
    )
