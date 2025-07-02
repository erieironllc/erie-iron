import json
import logging
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

from django.db import transaction

from erieiron_autonomous_agent.business_level_agents.self_driving_coder import agent_tools
from erieiron_autonomous_agent.business_level_agents.self_driving_coder.self_driving_coder_config import SelfDriverConfig, AgentBlocked
from erieiron_common import common
from erieiron_common.enums import TaskStatus, TaskExecutionMode
from erieiron_common.models import TaskExecution, SelfDrivingTaskIteration, CodeFile


def execute(iteration_id: uuid.UUID, env: str = "dev", logfile=None) -> Optional[TaskExecution]:
    iteration = SelfDrivingTaskIteration.objects.get(id=iteration_id)
    business = iteration.task.business
    output = None

    self_driving_task = iteration.task
    task = self_driving_task.related_task

    config = SelfDriverConfig.get(
        self_driving_task.config_path,
        self_driving_task.related_task_id
    )
    logging.info(f"executing {config.main_code_file.get_path()}")

    if self_driving_task.get_require_tests():
        test_exit_code = common.exec_cmd(
            f"pytest -v {config.main_code_file_test.get_path()}",
            logfile
        )
        if test_exit_code != 0:
            return None

    if not task:
        run_module_locally(
            iteration,
            config.main_code_file
        )
        return None
    else:
        task_input = {}
        for upstream_task in task.depends_on.all():
            if not TaskStatus.COMPLETE.eq(upstream_task.status):
                task.status = TaskStatus.BLOCKED
                task.save()
                raise Exception(f"task {task.id} depends on task {upstream_task.id}, but the upstream task's status is {upstream_task.status}")

            previous_task_execution = upstream_task.get_last_execution()
            if not previous_task_execution:
                raise AgentBlocked({
                    "desc": f"task {task.id} depends on upstream task {upstream_task.id}, but the upstream task has not executed"
                })

            task_input[upstream_task.id] = previous_task_execution.output

        output = None
        te = task.create_execution(task_input)

        includes_boostrap = "agent_tools.clone_template_project_to_sandbox(" in config.main_code_file.get_latest_version().code
        try:
            MAX_RETRIES = 3
            DELAY_SECONDS = 30
            for attempt in range(MAX_RETRIES):
                try:
                    if includes_boostrap or TaskExecutionMode.HOST.eq(task.execution_mode):
                        output = run_module_locally(
                            iteration,
                            config.main_code_file,
                            task_input
                        )
                    elif TaskExecutionMode.CONTAINER.eq(task.execution_mode):
                        output = run_module_in_docker(
                            env,
                            iteration,
                            config.main_code_file,
                            task_input,
                            logfile
                        )
                    else:
                        raise ValueError(f"unsupported execution_mode for {task.id}: {task.execution_mode}")
                    break  # successful execution, exit retry loop
                except agent_tools.PermissionEscalationRequired as e:
                    if attempt < MAX_RETRIES - 1:
                        logging.warning(f"Permission escalation occurred: {e}. Retrying in {DELAY_SECONDS} seconds...")
                        time.sleep(DELAY_SECONDS)
                    else:
                        raise

            status = TaskStatus.COMPLETE
            error_msg = None
        except Exception as e:
            logging.exception(e)
            status = TaskStatus.FAILED
            error_msg = str(e)

        te.resolve(output, status, error_msg)

        return te



def run_module_in_docker(
        env: str,
        iteration: SelfDrivingTaskIteration,
        code_file: CodeFile,
        task_input: dict,
        logfile=None
) -> dict:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        executable_file = tmp_path / code_file.get_path().name
        input_file = tmp_path / "input.json"
        output_file = tmp_path / "output.json"

        executable_file.write_text(
            iteration.get_code_version(code_file).code
        )

        with input_file.open("w") as f:
            json.dump(task_input, f)

        cmd = [
            "docker", "run", "--rm",
            "-v", f"{tmp_path}:/sandbox",
            "-e", f"ENV={env}",
            "-e", "RUNNING_IN_CONTAINER=true",
            f"erie_iron:{env}",
            "python", f"/sandbox/{executable_file.name}"
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            raise Exception(f"Docker execution failed: {result.stderr.strip()}")

        if not output_file.exists():
            raise Exception("Docker completed but output file was not created.")

        with output_file.open("r") as f:
            return json.load(f)


def run_module_locally(
        iteration: SelfDrivingTaskIteration,
        code_file: CodeFile,
        task_input: dict = None
) -> dict:
    code_version = iteration.get_code_version(code_file)
    # code_module = common.import_module_from_string(code_version.code)
    code_module = common.import_module_from_path(code_version.write_to_disk())
    return code_module.execute(task_input or {})
