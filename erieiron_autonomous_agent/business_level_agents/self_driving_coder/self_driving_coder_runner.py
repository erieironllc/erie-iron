import json
import logging
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

from erieiron_autonomous_agent.business_level_agents.self_driving_coder import agent_tools
from erieiron_autonomous_agent.business_level_agents.self_driving_coder.self_driving_coder_config import SelfDriverConfig
from erieiron_common import common
from erieiron_common.enums import TaskStatus, TaskExecutionMode
from erieiron_common.models import TaskExecution, SelfDrivingTaskIteration, CodeFile


def execute(task_execution_id: uuid.UUID, env: str = "dev", logfile=None) -> Optional[TaskExecution]:
    task_execution = TaskExecution.objects.get(id=task_execution_id)
    iteration = task_execution.iteration
    business = task_execution.task.initiative.business
    self_driving_task = iteration.self_driving_task
    task = self_driving_task.task

    config = SelfDriverConfig.get(
        self_driving_task.config_path,
        self_driving_task.task_id
    )
    logging.info(f"executing {config.main_code_file.get_path()}")

    output = None
    try:
        if self_driving_task.get_require_tests():
            test_exit_code = common.exec_cmd(
                f"pytest -v {config.main_code_file_test.get_path()}",
                logfile
            )
            if test_exit_code != 0:
                raise Exception(f"pytest exited with exit code {test_exit_code} signalling failure")

        MAX_RETRIES = 3
        DELAY_SECONDS = 30
        for attempt in range(MAX_RETRIES):
            try:
                includes_boostrap = "agent_tools.clone_template_project_to_sandbox(" in config.main_code_file.get_latest_version().code
                if True or includes_boostrap or TaskExecutionMode.HOST.eq(task.execution_mode):
                    output = run_module_locally(
                        iteration,
                        config.main_code_file,
                        task_execution.input
                    )
                elif TaskExecutionMode.CONTAINER.eq(task.execution_mode):
                    output = run_module_in_docker(
                        env,
                        iteration,
                        config.main_code_file,
                        task_execution.input,
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

    task_execution.resolve(
        output,
        status,
        error_msg
    )


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

        wrapper_file = tmp_path / "runner.py"
        wrapper_file.write_text(f"""
import json
import sys
import {executable_file.stem}

result = {executable_file.stem}.execute(json.load(open("input.json")))
json.dump(result, open("output.json", "w"))
""")

        cmd = [
            "docker", "run", "--rm",
            "-v", f"{tmp_path}:/sandbox",
            "-e", f"ENV={env}",
            "-e", "RUNNING_IN_CONTAINER=true",
            f"erie_iron:{env}",
            "python", "-u", "/sandbox/runner.py"
        ]

        result = subprocess.run(cmd, text=True)

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
