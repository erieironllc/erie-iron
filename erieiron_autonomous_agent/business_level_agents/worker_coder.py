import json

from erieiron_common import common
from erieiron_common.models import Task


def do_work(task_id):
    task = Task.objects.get(id=task_id)
    sandbox = task.product_initiative.business.get_sandbox_dir()

    config = common.get_dict(task)
    config["main_file"] = sandbox / "main.py"
    config["max_budget_usd"] = 100

    config_file = sandbox / "config.json"
    log_file = sandbox / "output.log"
    common.write_json(config_file, config)

    common.execute_management_cmd(
        f"exec_self_driver --config={config_file}",
        log_file
    )
