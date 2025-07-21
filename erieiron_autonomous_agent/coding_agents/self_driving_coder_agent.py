import json
import logging
import os
import pprint
import random
import subprocess
import tempfile
import time
import traceback
import zipfile
from pathlib import Path
from typing import List, Optional

from django.db import transaction
from django.db.models import Q
from django.db.models.expressions import RawSQL
from django.utils import timezone

import settings
from erieiron_autonomous_agent.enums import TaskStatus
from erieiron_autonomous_agent.models import CodeVersion, CodeMethod, SelfDrivingTaskIteration, LlmRequest, Task, RunningProcess, SelfDrivingTask, Business, CodeFile, TaskExecution
from erieiron_autonomous_agent.utils.codegen_utils import CodeCompilationError, get_codebert_embedding
from erieiron_common import common, settings_common
from erieiron_common.aws_utils import get_aws_interface
from erieiron_common.enums import LlmModel, S3Bucket, PubSubMessageType, TaskType, TaskExecutionSchedule
from erieiron_common.llm_apis import llm_interface
from erieiron_common.llm_apis.llm_constants import CODE_PLANNING_MODELS_IN_ORDER
from erieiron_common.llm_apis.llm_interface import LlmMessage, MODEL_TO_MAX_TOKENS, LlmResponse
from erieiron_common.message_queue.pubsub_manager import PubSubManager

PROMPTS_DIR = Path(__file__).parent / "prompts"

COUNT_FULL_LOGS_IN_CONTEXT = 2

MAP_TASKTYPE_TO_PLANNING_PROMPT = {
    TaskType.CODING_ML: "codeplanner--ml_trainer.md",
    TaskType.CODING_APPLICATION: "codeplanner--feature_development.md",
    TaskType.TASK_EXECUTION: "codeplanner--executable_task.md",
}

ARTIFACTS = "artifacts"

# next steps - separate out evaluation from planning.  use evaluation to identify files to bring into the context.  bring all files from all task iterations into the context

class GoalAchieved(Exception):
    def __init__(self, planning_data):
        self.planning_data = planning_data


class AgentBlocked(Exception):
    def __init__(self, blocked_data):
        self.blocked_data = blocked_data


class SelfDriverConfig:
    def __init__(self, self_driving_task: SelfDrivingTask):
        self.debug = True
        self.self_driving_task: SelfDrivingTask = self_driving_task
        self.task: Task = self_driving_task.task
        self.task_type: TaskType = TaskType(self.task.task_type)
        self.budget: float = self.task.max_budget_usd or 0
        self.business = Business.objects.get(initiative__tasks__id=self.task.id)
        self.guidance = LlmMessage.sys(self.task.guidance) if self.task.guidance else None
        self.sandbox_root_dir = Path(self.self_driving_task.sandbox_path)
        self.previous_iteration = self.self_driving_task.get_most_recent_iteration()
        self.current_iteration = None
        
        artifacts_root = self.sandbox_root_dir / ARTIFACTS
        artifacts_root.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir = artifacts_root
        self.log_path = artifacts_root / f"{self.self_driving_task.id}.output.log"
        self.git = self.self_driving_task.get_git()
        
        self.code_planning_model = random.choice(CODE_PLANNING_MODELS_IN_ORDER) # LlmModel.OPENAI_GPT_4_1_MINI  # random.choice(CODE_PLANNING_MODELS_IN_ORDER)
        self.code_writing_model = LlmModel.OPENAI_O3_MINI # LlmModel.OPENAI_GPT_4_1_MINI  # LlmModel.OPENAI_O3_MINI
    
    def initialize_new_iteration(self) -> SelfDrivingTaskIteration:
        self.current_iteration = self.self_driving_task.iterate()
        return self.current_iteration


def execute(task_id: str):
    self_driving_task = bootstrap_selfdriving_agent(task_id)
    
    config = None
    stop_reason = ""
    supress_eval = False
    try:
        for i in range(100):
            config = SelfDriverConfig(self_driving_task)
            try:
                if config.budget and config.self_driving_task.get_cost() > config.budget:
                    stop_reason = f"Stopping - hit the max budget ${config.budget :.2f}"
                    break
                
                config.initialize_new_iteration()
                
                log_iteration_headline(config)
                
                planning_data = evaluate_and_plan_code_changes(
                    config
                )
                pprint.pprint(planning_data)
                
                generate_code(
                    config,
                    planning_data
                )
                
                log_output = None
                try:
                    # next step is to figure out bootstrap project database 
                    log_output = execute_iteration(
                        config
                    )
                finally:
                    print(log_output)
                    post_process_iteration_execution(
                        config,
                        log_output
                    )
                
            
            except AgentBlocked as agent_blocked:
                pprint.pprint(agent_blocked.blocked_data)
                stop_reason = "Agent Blocked"
                if config.self_driving_task.task_id:
                    with transaction.atomic():
                        Task.objects.filter(id=config.self_driving_task.task_id).update(
                            status=TaskStatus.BLOCKED
                        )
                    
                    PubSubManager.publish(
                        PubSubMessageType.TASK_BLOCKED,
                        payload={
                            **agent_blocked.blocked_data,
                            "task_id": config.self_driving_task.task_id
                        }
                    )
                
                break
            except GoalAchieved as goal_achieved:
                config.git.add_commit_push(f"task {config.task.id}: {config.task.description}")
                
                stop_reason = "Goal Achieved"
                if config.self_driving_task.task_id:
                    PubSubManager.publish_id(
                        PubSubMessageType.TASK_COMPLETED,
                        config.self_driving_task.task_id
                    )
                
                break
            except Exception as e:
                logging.exception(e)
                config.supress_eval = True
                if config.self_driving_task.task_id:
                    pass
                    # PubSubManager.publish(
                    #     PubSubMessageType.TASK_FAILED,
                    #     payload={
                    #         "task_id": config.self_driving_task.task_id,
                    #         "error": traceback.format_exc()
                    #     }
                    # )
                
                break
    
    finally:
        print("DIR", config.git.source_root)
        # config.git.cleanup()
        
        print("STOP REASON", stop_reason)
        if TaskType.CODING_ML.eq(config.task_type):
            package_ml_artifacts(config)


def log_iteration_headline(config):
    iteration_count = config.self_driving_task.selfdrivingtaskiteration_set.count()
    headline = f"""
--------------------------------------------------
{timezone.now().strftime("%m/%d/%Y %H:%M:%S")}

Task id {config.task.id} 
iteration id {config.current_iteration.id} (v{iteration_count})
sandbox root dir: {os.path.abspath(config.sandbox_root_dir)}  
total spend: ${config.self_driving_task.get_cost() :.2f}/${config.budget :.2f}
tail -f {os.path.abspath(config.log_path)}

https://www.youtube.com/watch?v=-Ca-2FRsTx8&t=281s
--------------------------------------------------
                                    """
    print(headline)
    log(config, headline)


def bootstrap_selfdriving_agent(task_id) -> SelfDrivingTask:
    task = Task.objects.get(id=task_id)
    self_driving_task = task.create_self_driving_env()
    
    git = self_driving_task.get_git()
    git.pull()
    
    return self_driving_task


def build_docker_image(self_driving_task: SelfDrivingTask, log_f) -> str:
    docker_image = f"self_driving_task--{self_driving_task.id}"
    sandbox_path = self_driving_task.sandbox_path
    
    log_f.write("=" * 50 + "\n")
    log_f.write("BUILDING DOCKER IMAGE\n")
    log_f.write("=" * 50 + "\n")
    log_f.flush()
    
    build_cmd = [
        "docker",
        "build",
        "-t", docker_image,
        "--build-arg", f"GITHUB_TOKEN={os.environ.get('GITHUB_TOKEN', '')}",
        "-f", f"{sandbox_path}/Dockerfile",
        sandbox_path
    ]
    
    build_process = subprocess.Popen(
        build_cmd,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        text=True
    )
    
    while build_process.poll() is None:
        time.sleep(1)
    
    if build_process.returncode != 0:
        raise Exception(f"Docker build failed with return code: {build_process.returncode}")
    
    return docker_image


def run_docker_command(
        command_args: list[str],
        iteration: SelfDrivingTaskIteration,
        running_process: RunningProcess,
        docker_image: str,
        log_f
) -> None:
    task_execution = running_process.task_execution
    selfdriving_task = iteration.self_driving_task
    sandbox_path = iteration.self_driving_task.sandbox_path
    
    log_f.write("\n" + "=" * 50 + "\n")
    log_f.write("=" * 50 + "\n")
    log_f.flush()
    
    cmd = [
              "docker", "run", "--rm",
              "-v", f"{sandbox_path}:/app",
              "-w", "/app",
              docker_image,
              "python", "manage.py"
          ] + common.safe_strs(command_args)
    
    log_f.write(f"RUNNING {' '.join(cmd)} in {sandbox_path}\n")
    process = subprocess.Popen(
        cmd,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        text=True
    )
    
    # Update running process with PID
    running_process.process_id = process.pid
    running_process.save(update_fields=['process_id'])
    
    print(f"Docker {command_args[-1]} execution started with PID {process.pid}, iteration_id: {iteration.id}")
    
    # Wait for completion
    while process.poll() is None:
        running_process.update_log_tail()
        time.sleep(2)
    
    return_code = process.returncode
    log_f.write(f"\n{command_args[-1]} execution completed with return code: {return_code}\n")
    log_f.flush()
    
    running_process.update_log_tail()
    
    if return_code != 0:
        with open(running_process.log_file_path, "r") as log_read:
            error_output = log_read.read()
        task_execution.error_msg = error_output
        task_execution.save()
        raise Exception(f"Docker execution failed - return code: {return_code}")


def execute_iteration(config: SelfDriverConfig) -> str:
    iteration = config.current_iteration
    logfile = common.create_temp_file(f"iteration-{str(iteration.id)}", ".execution.log")
    running_process = None
    
    self_driving_task = iteration.self_driving_task
    task = self_driving_task.task
    task_type = TaskType(task.task_type)
    task_execution = init_task_execution(iteration)
    
    try:
        running_process, _ = RunningProcess.objects.update_or_create(
            task_execution=task_execution,
            execution_type='docker',
            log_file_path=str(logfile)
        )
        
        with open(logfile, "w") as log_f:
            docker_image = build_docker_image(
                self_driving_task,
                log_f
            )
            
            if TaskType.CODING_ML.eq(task_type):
                run_docker_command(
                    command_args=self_driving_task.main_name,
                    iteration=iteration,
                    running_process=running_process,
                    docker_image=docker_image,
                    log_f=log_f
                )
            else:
                run_docker_command(
                    command_args="test",
                    iteration=iteration,
                    running_process=running_process,
                    docker_image=docker_image,
                    log_f=log_f
                )
                
                # NEXT Step:  RUN THIS SHIT and see if it works!
                
                if TaskType.TASK_EXECUTION.eq(task_type) and TaskExecutionSchedule.ONCE.eq(task.execution_schedule):
                    task_io_dir = Path(self_driving_task.sandbox_path) / "task_io"
                    task_io_dir.mkdir(parents=True, exist_ok=True)
                    
                    input_file = task_io_dir / f"{task.id}-input.json"
                    common.write_json(input_file, task.get_upstream_outputs())
                    
                    output_file = task_io_dir / f"{task.id}-output.json"
                    
                    run_docker_command(
                        command_args=[
                            self_driving_task.main_name,
                            "--input_file", input_file,
                            "--output_file", output_file
                        ],
                        iteration=iteration,
                        running_process=running_process,
                        docker_image=docker_image,
                        log_f=log_f
                    )
            
            running_process.update_log_tail()
            running_process.is_running = False
            running_process.terminated_at = common.get_now()
            running_process.save(update_fields=['is_running', 'terminated_at'])
        
        log_output = logfile.read_text()
        log(config, log_output, f"iteration-{iteration.id}")
        
        print(f"Docker execution finished, log: {logfile}, iteration_id: {iteration.id}")
        
        return log_output
    except Exception as e:
        # Mark process as failed if it exists
        if running_process and running_process.is_running:
            running_process.is_running = False
            running_process.terminated_at = common.get_now()
            running_process.save(update_fields=['is_running', 'terminated_at'])
        
        log_output = logfile.read_text()
        log_output = f"{log_output}\n\n\nthrew:\n{traceback.format_exc()}"
        log(config, log_output, f"iteration-{iteration.id}")
        logging.exception(log_output)
    finally:
        common.quietly_delete(logfile)


def init_task_execution(iteration):
    task = iteration.self_driving_task.task
    
    task_input = {}
    for upstream_task in task.depends_on.all():
        if not TaskStatus.COMPLETE.eq(upstream_task.status):
            raise AgentBlocked(f"task {task.id} depends on task {upstream_task.id}, but the upstream task's status is {upstream_task.status}")
        
        previous_task_execution = upstream_task.get_last_execution()
        if not previous_task_execution:
            raise AgentBlocked({
                "desc": f"task {task.id} depends on upstream task {upstream_task.id}, but the upstream task has not executed"
            })
        
        task_input[upstream_task.id] = previous_task_execution.output
    
    return task.create_execution(
        input_data=task_input,
        iteration=iteration
    )


def post_process_iteration_execution(config, log_output):
    iteration = config.current_iteration
    with transaction.atomic():
        SelfDrivingTaskIteration.objects.filter(id=iteration.id).update(
            log_content=log_output or "no log output"
        )
    
    # if artifacts dir is empty, delete it
    artifacts_dir = Path(config.artifacts_dir)
    artifacts_parent_dir = artifacts_dir.parent
    
    assert artifacts_dir.name == ARTIFACTS
    
    if artifacts_dir.exists() and not any(artifacts_dir.iterdir()):
        artifacts_dir.rmdir()
    
    if artifacts_parent_dir.exists() and not any(artifacts_parent_dir.iterdir()):
        artifacts_parent_dir.rmdir()


def build_iteration_context_messages(config: SelfDriverConfig) -> List[LlmMessage]:
    messages = [
        get_goal_msg(config)
    ]
    
    eval_json = common.get(config, ["previous_iteration", "evaluation_json"], {})
    if TaskType.CODING_ML.eq(config.self_driving_task.task.task_type) and isinstance(eval_json, dict):
        previous_iteration_count = eval_json.get("previous_iteration_count", 1)
        if previous_iteration_count == "all":
            previous_iteration_count = config.self_driving_task.selfdrivingtaskiteration_set.count()
    else:
        previous_iteration_count = 2
    
    previous_iteration_count = min(5, previous_iteration_count)
    
    previous_iterations: list[SelfDrivingTaskIteration] = list(reversed(config.self_driving_task.selfdrivingtaskiteration_set.order_by("-timestamp")[0:previous_iteration_count]))
    for task_iteration in previous_iterations:
        if task_iteration == config.current_iteration:
            continue
        
        for code_version in task_iteration.codeversion_set.all():
            if Path(code_version.code_file.file_path).exists():
                messages.append(
                    LlmMessage.assistant(
                        f"iteration id={str(task_iteration.id)} code_file_path {code_version.code_file.file_path}",
                        code_version.code_file.file_path
                    )
                )
        
        task_execution: TaskExecution = task_iteration.taskexecution_set.last()
        if task_execution and task_execution.error_msg:
            messages.append(LlmMessage.user(
                f"""
            This is the log output from executing iteration id={str(task_iteration.id)}: 
            {task_execution.error_msg}
                    """
            ))
        
        
        eval_json = task_iteration.evaluation_json or {}
        evaluation = None
        instructions = None
        if isinstance(eval_json, dict):
            try:
                evaluation = eval_json['evaluation']
                instructions = eval_json['instructions']
            except Exception as e:
                pass
        elif isinstance(eval_json, list):
            evaluation = eval_json
        
        if evaluation:
            messages.append(
                LlmMessage.user(
                    f"""
            These are the results of executing iteration id={str(task_iteration.id)}: 
            {json.dumps(evaluation, indent=4)}
                    """
                )
            )
        
        if instructions:
            messages.append(
                LlmMessage.assistant(
                    f"""
            I have evaluated the execution results for iteration id={str(task_iteration.id)}.  Please make the following modifications:
            {json.dumps(instructions, indent=4)}
                    """
                )
            )
        
        if not (evaluation or instructions):
            messages.append(
                LlmMessage.user(
                    f"""
            The output of executing iteration id={str(task_iteration.id)} is as follows:
            {task_iteration.log_content}
                    """
                )
            )
    
    return messages


def get_sys_prompt(
        file_name: str,
        replacements: tuple[str, str] = None
) -> LlmMessage:
    return_list = common.is_list_like(file_name)
    
    messages = []
    for f in common.ensure_list(file_name):
        msg = (PROMPTS_DIR / f).read_text()
        for look_for_str, replace_with_str in common.ensure_list(replacements):
            msg = msg.replace(look_for_str, replace_with_str)
        messages.append(LlmMessage.sys(msg))
    
    if return_list:
        return messages
    else:
        return common.first(messages)


def generate_code(config: SelfDriverConfig, planning_data: dict) -> SelfDrivingTaskIteration:
    iteration = config.current_iteration
    
    iteration_id_to_modify = planning_data.get("iteration_id_to_modify", "latest")
    try:
        iteration_to_modify = config.self_driving_task.selfdrivingtaskiteration_set.get(id=iteration_id_to_modify)
    except:
        iteration_to_modify = config.previous_iteration
    
    evaluation_of_previous_code = planning_data.get("evaluation", [])
    if not iteration_to_modify:
        iteration_to_modify = iteration
    
    code_file_instructions = planning_data.get("code_files", [])
    if not code_file_instructions:
        raise Exception("no code files found")
    
    for cfi in code_file_instructions:
        code_file_path_str: str = cfi.get("code_file_path")
        if code_file_path_str.startswith(str(config.sandbox_root_dir)):
            code_file_path_str = code_file_path_str[len(str(config.sandbox_root_dir)) + 1:]
        
        code_file_path: Path = config.sandbox_root_dir / code_file_path_str
        if not code_file_path:
            raise Exception(f"missing code file name: {json.dumps(cfi)}")
        
        if not code_file_path.exists():
            code_file_path.parent.mkdir(parents=True, exist_ok=True)
            code_file_path.touch()
        
        code_version_to_modify = iteration_to_modify.get_code_version(
            code_file_path
        )
        code_file = code_version_to_modify.code_file
        
        instructions = cfi.get("instructions", [])
        if not instructions:
            print(f"no modifications for {code_file_path}")
            code_file.update(
                iteration,
                code_version_to_modify.code
            )
        else:
            previous_exception = None
            code_str = None
            for i in range(3):
                try:
                    previous_exception = None
                    code_str = get_coding_llm_response(
                        config=config,
                        code_version_to_modify=code_version_to_modify,
                        evaluation_of_previous_code=evaluation_of_previous_code,
                        instructions=instructions,
                        previous_exception=previous_exception
                    ).text
                    
                    break
                except CodeCompilationError as e:
                    previous_exception = e
            
            if previous_exception:
                raise previous_exception
            
            if code_str:
                code_file.update(
                    iteration,
                    code_str,
                    code_instructions=instructions
                )
    
    # NEXT STEP CONTINUE DEBUGGING 
    return iteration


def evaluate_and_plan_code_changes(config):
    iteration = config.current_iteration
    model = config.code_planning_model
    business = config.self_driving_task.business
    
    task = config.self_driving_task.task
    task_type = TaskType(task.task_type)
    
    messages = [
        *common.ensure_list(get_sys_prompt(
            [
                "codeplanner--base.md",
                MAP_TASKTYPE_TO_PLANNING_PROMPT[task_type]
            ],
            [
                ("<aws_tag>", str(business.service_token)),
                ("<db_name>", str(business.service_token)),
                ("<iam_role_name>", str(business.get_iam_role_name())),
                ("<artifacts_directory>", str(config.artifacts_dir)),
                ("<sandbox_dir>", str(config.sandbox_root_dir))
            ]
        )),
        *common.ensure_list(
            config.guidance
        ),
        *common.ensure_list(
            get_dependencies_msg(config, for_planning=True)
        ),
        *common.ensure_list(
            get_likely_code_files(config)
        ),
        *common.ensure_list(
            build_iteration_context_messages(config)
        ),
        *common.ensure_list(
            LlmMessage.user(
                f"""
This is the first attempt at implementing this task.  Please take your time to think of the best initial architecture.
Identify an architecture that will allow for efficient code iteration and give us the best start towards achieving the user's GOAL. 
                """)
            if not config.previous_iteration else None
        )
    ]
    
    files = common.filter_none([m.file for m in messages])
    if files:
        print("FILES IN THE CONTEXT")
        for f in files:
            print(f"cat {os.path.abspath(f)}")
        print(" ")
        print(" ")
    
    token_count = LlmMessage.get_total_token_count(model, messages)
    max_tokens = MODEL_TO_MAX_TOKENS.get(model)
    
    if max_tokens:
        print(f"about to call out to {model} to plan optimizations.  {token_count:,}/{max_tokens:,} tokens used")
    else:
        print(f"about to call out to {model} to plan optimizations.  {token_count:,} tokens used")
    
    log_llm_request(config, messages)
    llm_response_planning = llm_interface.chat(
        messages,
        model,
        output_schema=PROMPTS_DIR / "codeplanner.schema.json",
        code_response=True
    )
    
    log_llm_response(config, llm_response_planning)
    print(f"\t\tcost of planning: total ${llm_response_planning.price_total:.4f}; input ${llm_response_planning.price_input:.4f}; output ${llm_response_planning.price_output:.4f}")
    
    planning_data, post_process_price = ensure_parsable_json(
        config,
        llm_response_planning.text
    )
    
    planning_data['planning_model'] = str(config.code_planning_model)
    goal_achieved = common.parse_bool(planning_data.get('goal_achieved'))
    blocked_data = planning_data.get('blocked')
    
    with transaction.atomic():
        SelfDrivingTaskIteration.objects.filter(id=iteration.id).update(
            execute_module=planning_data.get('execute_module'),
            test_module=planning_data.get('test_module')
        )
        iteration.refresh_from_db(fields=["execute_module", "test_module"])
    
    if config.previous_iteration:
        with transaction.atomic():
            SelfDrivingTaskIteration.objects.filter(id=config.previous_iteration.id).update(
                achieved_goal=goal_achieved,
                evaluation_json=planning_data
            )
    
    if blocked_data:
        raise AgentBlocked(blocked_data)
    
    if goal_achieved:
        raise GoalAchieved(planning_data)
    
    return planning_data


def ensure_parsable_json(config, json_text: str) -> dict:
    orig_json_text = json_text
    price = 0
    last_e = None
    for i in range(5):
        if not json_text:
            raise Exception(f"json_text is empty")
        
        while len(json_text) > 0 and json_text[0] != "{":
            json_text = json_text[1:]
        
        while len(json_text) > 0 and json_text[-1] != "}":
            json_text = json_text[:-1]
        
        if common.is_empty(json_text):
            raise Exception(f"unable to parse json\n{orig_json_text}")
        
        try:
            parsed_text = json.loads(json_text)
            return parsed_text, price
        except Exception as e:
            # print(f"----------\n{json_text}\n\n{e}\n--------------")
            
            last_e = e
            llm_response_reformat = llm_interface.chat(
                f"""
please format and return the following json text as valid and parsable json:

========= json text start ================
{json_text}
========= json text end ================


the previous attempt at parsing this content resulted in this error:  {e}


resond only with parsable json.  do not include any comments, explanations, or non-json markdown
""",
                LlmModel.OPENAI_O3_MINI,
                code_response=True
            )
            price += llm_response_reformat.price_total
            log_llm_response(config, llm_response_reformat, suppress_log=True)
            json_text = llm_response_reformat.text
    
    raise last_e


def get_coding_llm_response(
        config: SelfDriverConfig,
        code_version_to_modify: CodeVersion,
        evaluation_of_previous_code,
        instructions,
        previous_exception: Optional[CodeCompilationError]
) -> LlmResponse:
    model = config.code_writing_model
    
    code_file_name = code_version_to_modify.code_file.get_path().name
    if code_file_name == "requirements.txt":
        prompt = "codewriter--requirements.txt.md"
    elif code_file_name.startswith("cloudformation") and code_file_name.endswith(".yaml"):
        prompt = "codewriter--aws_cloudformation_coder.md"
    elif code_file_name.startswith("Dockerfile"):
        prompt = "codewriter--dockerfile_coder.md"
    elif code_file_name.endswith(".py"):
        prompt = "codewriter--python_coder.md"
    elif code_file_name.endswith(".sql"):
        prompt = "codewriter--sql_coder.md"
    elif code_file_name.endswith(".js"):
        prompt = "codewriter--javascript_coder.md"
    elif code_file_name.endswith(".html"):
        prompt = "codewriter--html_coder.md"
    elif code_file_name.endswith(".css"):
        prompt = "codewriter--css_coder.md"
    else:
        raise Exception(f"no coder implemented for {code_file_name}")
    
    messages: list[LlmMessage] = [
        *common.ensure_list(
            get_sys_prompt(
                prompt,
                ("<sandbox_dir>", str(config.sandbox_root_dir))
            )),
        *common.ensure_list(
            get_dependencies_msg(config, for_planning=False)
        ),
        *common.ensure_list(
            get_likely_code_files(config)
        )
    ]
    
    if previous_exception:
        messages.append(
            LlmMessage.user(
                f"""
This is code from the previous attempt to generate code based on the instruction set:
{previous_exception.code_str}

This code as the following error(s): 
{str(previous_exception)}

Please try again, avoiding these errors
        """
            )
        )
    elif evaluation_of_previous_code and config.previous_iteration:
        messages.append(
            LlmMessage.assistant(
                f"""
Your evaluation of the previous iteration's (iteration id={str(config.previous_iteration.id)}) performance against the user's GOAL is as follows:

{json.dumps(evaluation_of_previous_code, indent=4)}
                """
            )
        )
    
    code_file_path = code_version_to_modify.code_file.file_path
    if code_version_to_modify.code:
        print("modifying", code_file_path)
        
        messages.append(
            LlmMessage.user(
                f"iteration id={str(code_version_to_modify.task_iteration.id)} code_file_path {code_file_path}",
                config.sandbox_root_dir / code_version_to_modify.code_file.file_path
            )
        )
        
        messages.append(
            LlmMessage.user(
                f"""
Modify {code_file_path}, following each of these instructions exactly and in order:

{json.dumps(instructions, indent=4)}
    """
            )
        )
    
    else:
        messages.append(
            LlmMessage.user(
                f"""
Please write the initial version of {code_file_path}, following each of these instructions exactly and in order:

{json.dumps(instructions, indent=4)}
        """
            )
        )
    
    token_count = LlmMessage.get_total_token_count(model, messages)
    max_tokens = MODEL_TO_MAX_TOKENS.get(model)
    
    if max_tokens:
        print(f"about to call out to {model} to generate code for {code_file_path}.  {token_count:,}/{max_tokens:,} tokens used")
    else:
        print(f"about to call out to {model} to generate code for {code_file_path}.  {token_count:,} tokens used")
    
    log_llm_request(config, messages)
    llm_response_codegen = llm_interface.chat(
        messages,
        model,
        code_response=True
    )
    
    log_llm_response(config, llm_response_codegen)
    
    print(f"\t\tcost of code gen: total ${llm_response_codegen.price_total:.4f}; input ${llm_response_codegen.price_input:.4f}; output ${llm_response_codegen.price_output:.4f}")
    
    return llm_response_codegen


def get_dependencies_msg(config: SelfDriverConfig, for_planning: bool) -> LlmMessage:
    header = "The python environment has the following packages installed"
    if for_planning:
        header += ".  If you need additional packages, you'll need to add them to the requirements.txt"
    
    return LlmMessage.sys(
        f"""
{header}
========== begin requirements.txt ================
{(config.sandbox_root_dir / 'requirements.txt').read_text()}
========== end requirements.txt ================
"""
    )


def get_helper_methods_msg(config) -> LlmMessage:
    return LlmMessage.sys(
        f"""
Helper Methods

You may use the helper methods defined in agent_tools.py
========== begin agent_tools.py ================
{(Path(__file__).parent / "agent_tools_stub.py").read_text()}
========== end agent_tools.py ================

If you use any of these helper methods, you must import agent_tools with the following line of code:
from erieiron_autonomous_agent.business_level_agents.self_driving_coder import agent_tools

if a helper method has a parameter named 'business_id', use the following value for business_id: "{config.self_driving_task.business_id or None}"
if a helper method has a parameter named 'task_id', use the following value for task_id: "{config.self_driving_task.task_id or None}"
"""
    )


def log_llm_request(config: SelfDriverConfig, llm_messages: list[LlmMessage]):
    config.log_path.touch(exist_ok=True)
    log(config, ">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>")
    log(config, f"LlmRequest")
    for m in common.ensure_list(llm_messages):
        log(config, m, prefix="\t")
        log(config, "\n")
    log(config, ">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>")


def log_llm_response(config, llm_response: LlmResponse, suppress_log=False):
    LlmRequest.objects.create(
        task_iteration=config.self_driving_task.get_most_recent_iteration(),
        token_count=llm_response.token_count,
        price=llm_response.price_total
    )
    
    if not suppress_log:
        config.log_path.touch(exist_ok=True)
        log(config, "<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<")
        log(config, f"Response from {llm_response.model.label()}")
        log(config, llm_response.text, prefix="\t")
        log(config, "<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<")
        log(config, "\n")


def log(config: SelfDriverConfig, s: str, prefix: str = None):
    prefix = prefix or ""
    config.log_path.touch(exist_ok=True)
    with open(config.log_path, 'a') as messages_log:
        for line in common.safe_split(s, "\n"):
            messages_log.write(f"{prefix}{line}\n")


def package_ml_artifacts(config: SelfDriverConfig):
    if not config:
        return
    
    if time.time() > 0:
        raise Exception("""
    NOTE FOR FUTURE SELF - THIS CODE WILL TAKE SOME ITERATION AND DEBUGGING TO GET  TO WORK
    """)
    
    messages = []
    
    messages.append(get_goal_msg(config))
    
    aval_iterations = []
    for best_iteration in config.self_driving_task.selfdrivingtaskbestiteration_set.filter(iteration__evaluation_json__isnull=False).order_by("timestamp"):
        aval_iterations.append(best_iteration.iteration_id)
    
    if not aval_iterations:
        aval_iterations = [
            i.id for i in config.self_driving_task.selfdrivingtaskiteration_set.filter(evaluation_json__isnull=False).order_by("timestamp")
        ]
    
    for iteration in SelfDrivingTaskIteration.objects.filter(id__in=aval_iterations).order_by("timestamp"):
        messages.append(
            LlmMessage.assistant(
                f"""
Iteration id={str(iteration.id)} ({iteration.timestamp}) results:
{json.dumps(iteration.evaluation_json, indent=4)}

NOTE: Iteration id={str(iteration.id)} {'successfully achieved the user''s GOAL' if iteration.achieved_goal else 'did not achieve the user''s GOAL'}  
                """
            )
        )
    
    aval_iteration_ids_str = ", ".join([str(s) for s in aval_iterations])
    
    if TaskType.CODING_ML.eq(config.task_type):
        messages.append(
            LlmMessage.sys(
                f"""
You are expert level at evaluating model training results and choosing the best result

YOUR TASKS:  
1)  Look through ALL of the attached iteration results and identify the id of the Iteration that BEST ACHIEVES THE GOAL
    -  if an iteration has one or more CRITICAL ISSUES, RuntimeError, or exceptions described in its results, it is excluded from consideration for best_iteration
    -  if there is a metric value associated with the GOAL, use the metric value to identify the best iteration
2)  Respond with a json dict in the following format:
    ========= example response format ===========
    {{
        "best_iteration_id": the uuid id representing the iteration that you think best achieves the GOAL.  must be one of these values: {aval_iteration_ids_str},
        "summary":  a summary of why you chose that iteration for best - if there is a metric you are using to rank the iterations and pick the best, name the metric and the best_iteration's metric value,
        "details":  markdown formatted details a) expanding on why you chose that iteration  and b) an evaluation on how that iteration's model would perform in a production environment,
        "curriculum":  markdown formatted teaching lessons gleened from all of the iterations that got us to the best iteration.  This can be a really long response with many subject sections.  The audience for the curriculum response is a junior engineer trying to learn all they can about high-level area (machine-learning for example).  the goal here is to help the junior engineer build out their mental model, so real world examples and leaning into the 'why' is important.  NOTE:  Please do not mention the word 'curriculum' or name the audience in this response
        
    }}
    ========= end example response format ===========

RESPOND ONLY WITH IMMEDIATELY PARSEABLE JSON IN THE EXAMPLE RESPONSE FORMAT
        """
            ))
    else:
        messages.append(
            LlmMessage.assistant(
                f"""
You are a principal level engineer who loves reviewing code and teaching about programming  

YOUR TASKS:  
1)  Look through ALL of the attached iteration results and identify the id of the Iteration that BEST ACHIEVES THE GOAL
    -  if an iteration has one or more CRITICAL ISSUES, RuntimeError, or exceptions described in its results, it is excluded from consideration for best_iteration
    -  if there is a metric value associated with the GOAL, use the metric value to identify the best iteration
2)  Respond with a json dict in the following format:
    ========= example response format ===========
    {{
        "best_iteration_id": the uuid id representing the iteration that you think best achieves the user's GOAL.  must be one of these values: {aval_iterations},
        "summary":  a summary of why you chose that iteration for best - if there is a metric you are using to rank the iterations and pick the best, name the metric and the best_iteration's metric value,
        "details":  markdown formatted details a) expanding on why you chose that iteration  and b) an evaluation on how the code would perform in a production environment,
        "curriculum":  markdown formatted teaching lessons gleened from all of iterations that got us to the best iteration.  This can be a really long response with many subject sections.  The audience for the curriculum response is a junior engineer trying to learn all they can about writing quality performant code.  the goal here is to help the junior engineer build out their mental model, so real world examples and leaning into the 'why' is important.  NOTE:  Please do not mention the word 'curriculum' or name the audience in this response
    }}
    ========= end example response format ===========

RESPOND ONLY WITH IMMEDIATELY PARSEABLE JSON IN THE EXAMPLE RESPONSE FORMAT
            """
            ))
    
    print(f"reviewing the {len(aval_iterations)} iterations and generating a final evaluation...")
    
    log_llm_request(config, messages)
    llm_response = llm_interface.chat(
        messages,
        config.code_planning_model,
        code_response=True
    )
    log_llm_response(config, llm_response)
    
    print(f"got llm response len: {len(llm_response.text)}")
    eval_data, _ = ensure_parsable_json(config, llm_response.text)
    
    best_iteration = SelfDrivingTaskIteration.objects.get(id=common.get(eval_data, 'best_iteration_id'))
    best_version_num = best_iteration.version_number
    
    code_file_name = config.self_driving_task.main_name
    code_file = CodeFile.get(config.business, code_file_name)
    main_file_name = code_file.get_base_name()
    
    code_versions: list[CodeVersion] = \
        list(best_iteration.codeversion_set.filter(code_file=code_file)) \
        + list(best_iteration.codeversion_set.exclude(code_file=code_file))
    
    best_iteration_code = []
    for cv in code_versions:
        best_iteration_code.append(f"""
======== start {cv.code_file.file_path}'s code =============
{cv.code}
======== end {cv.code_file.file_path}'s code =============
        """
                                   )
    best_iteration_code = "\n".join(best_iteration_code)
    
    messages = LlmMessage.user(f"""
    Please review the following code and write markdown formatted details fully explaining {main_file_name}'s model architecture and training strategy:
{best_iteration_code}

    Policies that must be followed:
        - give as much detail as needed.  
        - the audience for this is a mid-level ML research scientist.  If there are things you'd like to teach to a mid-level ML research scientist, please add this content as well
        - DO NOT include the string "```markdown" anywhere in the response
    """)
    
    log_llm_request(config, messages)
    arch_llm_response = llm_interface.chat(messages, config.code_planning_model)
    log_llm_response(config, arch_llm_response)
    
    planning_model = best_iteration.planning_model
    if planning_model:
        planning_model = f"({planning_model})"
    else:
        planning_model = ""
    
    curriculum = common.get(eval_data, 'curriculum', "")
    if curriculum:
        curriculum_lines = curriculum.split("\n")
        if common.default_str(curriculum_lines[0]).lower().startswith("### "):
            curriculum_lines = curriculum_lines[1:]
            curriculum = "\n".join(curriculum_lines)
    
    checkpoints_dir = config.artifacts_dir
    
    if checkpoints_dir:
        checkpoint_files = [f for f in Path(checkpoints_dir).iterdir() if f.is_file()]
        s3_dir = f"{main_file_name}/{main_file_name}_v{best_version_num}"
    else:
        checkpoint_files = []
        s3_dir = None
    
    for file in checkpoint_files:
        get_aws_interface().upload_file(
            file,
            settings_common.BUCKETS[S3Bucket.MODELS],
            f"{s3_dir}/{file.name}"
        )
    
    print(f"model checkpoints uploaded to s3://{settings_common.BUCKETS[S3Bucket.MODELS]}/{s3_dir}")
    
    txt_output = f"""
# Best iteration
Iteration #{best_version_num} {planning_model}
Code:  {main_file_name}
### Checkpoints:
s3://{settings_common.BUCKETS[S3Bucket.MODELS]}/{s3_dir}

# Summary
{common.get(eval_data, 'summary')}

# Details
{common.get(eval_data, 'details')}

# Architecture
{arch_llm_response.text}

# Notes
{curriculum}
    """
    
    archive_path = Path(settings.BASE_DIR) / f"task_{config.task.id}_v{best_version_num}.zip"
    with tempfile.TemporaryDirectory() as tmpdirname:
        with zipfile.ZipFile(archive_path, 'w') as zipf:
            tmpdirname = Path(tmpdirname)
            print(f"created temporary directory at {tmpdirname}")
            
            eval_text_path = tmpdirname / f"{config.task.id}.full-eval.md"
            eval_text_path.write_text(txt_output)
            zipf.write(
                eval_text_path,
                arcname=os.path.relpath(eval_text_path, tmpdirname)
            )
            
            zipf.write(
                config.log_path,
                arcname=config.log_path.name
            )
            
            for cv in best_iteration.codeversion_set.all():
                tmp_code_file = tmpdirname / cv.code_file.file_path
                tmp_code_file.parent.mkdir(parents=True, exist_ok=True)
                tmp_code_file.write_text(cv.code)
                zipf.write(
                    tmp_code_file,
                    arcname=os.path.relpath(tmp_code_file, tmpdirname)
                )
    
    common.quietly_delete(tmpdirname)
    print(txt_output)
    
    print(f"""grab artifacts from
scpc "{os.path.abspath(archive_path)}"
    """)
    
    config.self_driving_task.rollback_to(best_iteration)
    print(f"code updated to iteration v{best_version_num}")


def get_goal_msg(config):
    return LlmMessage.user(f"""
the user's GOAL is:
{config.self_driving_task.goal}""")


def get_likely_code_files(config: SelfDriverConfig) -> list[LlmMessage]:
    # these will be include later in the context
    existing_code_files = set()
    for cv in CodeVersion.objects.filter(task_iteration=config.previous_iteration):
        existing_code_files.add(cv.code_file_id)
    
    # Step 1: Get the structured retrieval cues from the LLM
    llm_response_cues = llm_interface.chat(
        [
            get_sys_prompt("codefinder.md"),
            LlmMessage.user(config.self_driving_task.task.get_work_desc())
        ],
        LlmModel.OPENAI_GPT_3_5_TURBO,
        output_schema=PROMPTS_DIR / "codefinder.md.schema.json",
        code_response=True
    )
    log_llm_response(config, llm_response_cues)
    
    cues = llm_response_cues.json()
    semantic_query = cues.get("semantic_query_sentence") or config.self_driving_task.task.get_work_desc()
    prompt_embedding = get_codebert_embedding(semantic_query).tolist()
    
    # Use a similarity threshold instead of top-k results
    # Lower similarity scores indicate higher similarity (cosine distance)
    # 0.3 is a reasonable threshold for similar code
    similarity_threshold = 0.3
    
    # For Python and JavaScript files, retrieve CodeMethod models for more granular search
    # For other file types, retrieve CodeVersion objects at the file level
    
    # First get code methods for Python and JavaScript files
    erie_common_code_methods = (
        CodeMethod.objects
        .select_related("code_version", "code_version__code_file")
        .filter(
            code_version__code_file__business=config.business,
        )
        .filter(
            Q(code_version__code_file__file_path__endswith=".py") |
            Q(code_version__code_file__file_path__endswith=".js")
        )
        .exclude(
            Q(code_version__code_file__file_path__startswith="env/") |
            Q(code_version__code_file__file_path__startswith="venv/")
        )
        .annotate(
            similarity=RawSQL("erieiron_codemethod.codebert_embedding <-> %s::vector", [prompt_embedding])
        )
        .filter(similarity__lte=similarity_threshold)
        .order_by("similarity")
    )
    
    # Find additional code methods that aren't from existing code files
    additional_code_methods = (
        CodeMethod.objects
        .select_related("code_version", "code_version__code_file")
        .filter(
            code_version__code_file__business=config.business,
        )
        .filter(
            Q(code_version__code_file__file_path__endswith=".py") |
            Q(code_version__code_file__file_path__endswith=".js")
        )
        .exclude(
            Q(code_version__code_file__file_path__startswith="env/") |
            Q(code_version__code_file__file_path__startswith="venv/")
        )
        .exclude(code_version__code_file__id__in=existing_code_files)
        .annotate(
            similarity=RawSQL("erieiron_codemethod.codebert_embedding <-> %s::vector", [prompt_embedding])
        )
        .filter(similarity__lte=similarity_threshold)
        .order_by("similarity")
    )
    
    # Process all code methods (original and additional)
    all_code_methods = list(erie_common_code_methods) + list(additional_code_methods)
    
    # Keep track of which code files we've already included to ensure only latest version per file
    processed_code_files = set()
    
    messages = []
    for code_method in all_code_methods:
        code_file = code_method.code_version.code_file
        
        # Skip if we've already processed this code file
        if code_file.id in processed_code_files:
            continue
        
        processed_code_files.add(code_file.id)
        
        file_path = code_file.file_path
        relative_path = file_path.split("erieiron_common")
        
        messages.append(LlmMessage.user(f"""
Possibly useful code method: "{code_method.name}"
You may use "{code_method.name}" but not modify it as it lives in imported package
"{code_method.name}" lives in the file "{relative_path}"

============= BEGIN {code_method.name} CODE =============
{code_method.code}
============= END {code_method.name} CODE =============
"""))
    
    # Then get code versions for other file types (excluding Python and JavaScript)
    # Also exclude files that are already included from previous iteration or code methods above
    all_excluded_code_files = set(existing_code_files) | processed_code_files
    
    code_versions_query = (
        CodeVersion.objects
        .exclude(code_file__id__in=all_excluded_code_files)
        .filter(code_file__business=config.business)
        .exclude(
            Q(code_file__file_path__endswith=".py") |
            Q(code_file__file_path__endswith=".js")
        )
        .annotate(
            similarity=RawSQL("codebert_embedding <-> %s::vector", [prompt_embedding])
        )
        .filter(similarity__lte=similarity_threshold)
        .order_by("similarity")
    )
    
    # Ensure only latest version per code file
    latest_code_versions = {}
    for cv in code_versions_query:
        code_file_id = cv.code_file.id
        if code_file_id not in latest_code_versions or cv.id > latest_code_versions[code_file_id].id:
            latest_code_versions[code_file_id] = cv
    
    code_versions = list(latest_code_versions.values())
    
    for code_version in code_versions:
        messages.append(LlmMessage.user(f"""
"{code_version.code_file.file_path}" is an existing project code file that you may use and or edit

============= BEGIN {code_version.code_file.file_path} CODE =============
{code_version.code}
============= END {code_version.code_file.file_path} CODE =============
        """))
    
    return messages
