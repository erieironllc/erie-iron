import json
import logging
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from erieiron_autonomous_agent.coding_agents.self_driving_coder_config import SelfDriverConfig
from erieiron_autonomous_agent.enums import TaskStatus
from erieiron_autonomous_agent.models import TaskExecution, SelfDrivingTaskIteration, CodeFile
from erieiron_common import common
from erieiron_common.enums import TaskType


def execute(task_execution_id: uuid.UUID, env: str = "dev", logfile=None) -> Optional[TaskExecution]:
    task_execution = TaskExecution.objects.get(id=task_execution_id)
    iteration = task_execution.iteration
    business = task_execution.task.initiative.business
    self_driving_task = iteration.self_driving_task
    task = self_driving_task.task
    task_type = TaskType(task.task_type)
    
    config = SelfDriverConfig.get(
        self_driving_task.config_path,
        self_driving_task.task_id
    )
    logging.info(f"executing {config.main_code_file.get_path()}")
    
    output = None
    try:
        if not TaskType.CODING_ML.eq(task_type):
            test_exit_code = common.exec_cmd(
                f"./venv/bin/pytest -v {config.main_code_file_test.get_path()}",
                logfile,
                cwd=self_driving_task.sandbox_path
            )
            if test_exit_code != 0:
                raise Exception(f"pytest exited with exit code {test_exit_code} signalling failure")
        
        MAX_RETRIES = 3
        DELAY_SECONDS = 30
        for attempt in range(MAX_RETRIES):
            output = run_module_locally(task_execution)
            
            break  # successful execution, exit retry loop
        
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


def run_module_locally(task_execution: TaskExecution) -> dict:
    iteration = task_execution.iteration
    sdt = task_execution.iteration.self_driving_task
    execute_module_file = common.assert_exists(Path(sdt.sandbox_path) / iteration.execute_module)
    
    # TODO fig
    code_module = common.import_module_from_path(execute_module_file)
    return code_module.execute(task_input or {})
