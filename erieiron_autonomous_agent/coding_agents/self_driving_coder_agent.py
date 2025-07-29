import json
import logging
import os
import pprint
import subprocess
import time
import traceback
from collections import defaultdict
from pathlib import Path
from typing import List, Optional

import boto3
from django.db import transaction
from django.db.models import Q
from django.db.models.expressions import RawSQL
from django.utils import timezone

from erieiron_autonomous_agent.enums import TaskStatus
from erieiron_autonomous_agent.models import CodeVersion, CodeMethod, SelfDrivingTaskIteration, LlmRequest, Task, RunningProcess, SelfDrivingTask, Business, CodeFile
from erieiron_autonomous_agent.utils.codegen_utils import CodeCompilationError, get_codebert_embedding
from erieiron_common import common, aws_utils
from erieiron_common.aws_utils import sanitize_aws_name
from erieiron_common.enums import LlmModel, PubSubMessageType, TaskType, TaskExecutionSchedule, AwsEnv
from erieiron_common.llm_apis import llm_interface
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


class GoalAchieved(Exception):
    def __init__(self, planning_data):
        self.planning_data = planning_data


class AgentBlocked(Exception):
    def __init__(self, blocked_data):
        self.blocked_data = blocked_data


class BadPlan(Exception):
    def __init__(self, msg: str, plan_data):
        self.plan_data = plan_data
        super(msg)


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
        self.current_iteration = None
        
        artifacts_root = self.sandbox_root_dir / ARTIFACTS
        artifacts_root.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir = artifacts_root
        self.log_path = artifacts_root / f"{self.self_driving_task.id}.llm.output.log"
        self.log_f = None
        self.git = self.self_driving_task.get_git()
        
        self.model_iteration_evaluation = LlmModel.OPENAI_GPT_4o_20240806
        self.model_code_planning = LlmModel.OPENAI_GPT_4o_20240806
        
        # self.model_iteration_evaluation = LlmModel.OPENAI_GPT_4_1_MINI
        # self.model_code_planning = LlmModel.OPENAI_GPT_4_1_MINI


def execute(task_id: str):
    self_driving_task = bootstrap_selfdriving_agent(task_id)
    
    config = None
    log_output = None
    skip_code_modification = False
    stop_reason = ""
    supress_eval = False
    try:
        for i in range(100):
            config = SelfDriverConfig(self_driving_task)
            current_iteration = None
            skip_code_modification = False
            
            try:
                if config.budget and config.self_driving_task.get_cost() > config.budget:
                    stop_reason = f"Stopping - hit the max budget ${config.budget :.2f}"
                    break
                
                if not (self_driving_task.design_doc_path and (config.sandbox_root_dir / self_driving_task.design_doc_path).exists()):
                    current_iteration, _, _ = self_driving_task.iterate()
                    write_initial_design(
                        config,
                        current_iteration
                    )
                
                if not (self_driving_task.test_file_path and (config.sandbox_root_dir / self_driving_task.test_file_path).exists()):
                    current_iteration, _, _ = self_driving_task.iterate()
                    if not config.business.codefile_set.exists():
                        config.business.snapshot_code(current_iteration, include_erie_common=True)
                    
                    log_initial_test_only_headline(
                        config,
                        current_iteration
                    )
                    
                    write_initial_test(
                        config,
                        current_iteration
                    )
                
                elif i == 0:
                    # we've re-started an self driving task - just execute on the first time around
                    current_iteration = self_driving_task.get_most_recent_iteration()
                    if not current_iteration:
                        current_iteration, _, _ = self_driving_task.iterate()
                    
                    SelfDrivingTaskIteration.objects.filter(id=current_iteration.id).update(
                        log_content_execution="",
                        evaluation_json=None
                    )
                    current_iteration.refresh_from_db(fields=["log_content_execution", "evaluation_json"])
                    
                    log_execution_only_headline(
                        config,
                        current_iteration
                    )
                else:
                    current_iteration, previous_iteration, iteration_to_modify = self_driving_task.iterate()
                    iteration_to_modify.write_to_disk()
                    
                    log_iteration_headline(
                        config,
                        current_iteration,
                        iteration_to_modify
                    )
                    
                    coding_logfile = common.create_temp_file(f"iteration-{str(current_iteration.id)}", ".coding.log")
                    with open(coding_logfile, "w") as coding_log_f:
                        logging.info(f"PHASE - get_relevant_code_files: {current_iteration.id}")
                        relevant_code_files = get_relevant_code_files(
                            config,
                            current_iteration,
                            iteration_to_modify
                        )
                        
                        logging.info(f"PHASE - plan_code_changes: {current_iteration.id}")
                        planning_data = plan_code_changes(
                            config,
                            current_iteration,
                            previous_iteration,
                            iteration_to_modify,
                            relevant_code_files
                        )
                        # pprint.pprint(planning_data)
                        
                        logging.info(f"PHASE - generate_code: {current_iteration.id}")
                        generate_code(
                            config,
                            current_iteration,
                            previous_iteration,
                            iteration_to_modify,
                            planning_data
                        )
                
                try:
                    try:
                        logging.info(f"PHASE - execute_iteration: {current_iteration.id}")
                        execute_iteration(
                            config,
                            current_iteration,
                            AwsEnv.DEV
                        )
                    finally:
                        logging.info(f"PHASE - evaluate_iteration_execution: {current_iteration.id}")
                        eval_data = None
                        try:
                            eval_data = evaluate_iteration_execution(
                                config,
                                current_iteration
                            )
                        finally:
                            if eval_data:
                                pprint.pprint(eval_data)
                
                except GoalAchieved as goal_achieved:
                    if TaskType.CODING_ML.eq(config.task_type):
                        raise goal_achieved
                    elif not self_driving_task.task.initiative.all_tasks_complete():
                        raise goal_achieved
                    else:
                        # for non-ml tasks, if all tasks are complete deploy to prod.  
                        # execute_and_evaluate will re-throw GoalAchieved if the prod deploy is successful
                        # perhaps think about moving this somewhere else - like listen to an event and then do this work in a separa message
                        prod_deploy_iteration, _, _ = self_driving_task.iterate()
                        execute_iteration(
                            config,
                            prod_deploy_iteration,
                            AwsEnv.PRODUCTION
                        )
            
            except BadPlan as bad_plan_exception:
                pprint.pprint(bad_plan_exception.plan_data)
                logging.exception(bad_plan_exception)
                with transaction.atomic():
                    SelfDrivingTaskIteration.objects.filter(id=current_iteration.id).update(
                        log_content_execution=f"""
                    Planning agent produced a bad plan:
                    {traceback.format_exc()}
                        """
                    )
                current_iteration.refresh_from_db(fields=["log_content_execution"])
            except AgentBlocked as agent_blocked:
                logging.exception(agent_blocked)
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
                            "blocked_data": json.dumps(agent_blocked.blocked_data),
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
                    # PubSubManager.publish(
                    #     PubSubMessageType.TASK_FAILED,
                    #     payload={
                    #         "task_id": config.self_driving_task.task_id,
                    #         "error": traceback.format_exc()
                    #     }
                    # )
                    ...
                
                break
    
    finally:
        print("DIR", config.git.source_root)
        # config.git.cleanup()
        
        print("STOP REASON", stop_reason)
        if TaskType.CODING_ML.eq(config.task_type):
            from erieiron_autonomous_agent.coding_agents.ml_packager import package_ml_artifacts
            package_ml_artifacts(config)


def log_iteration_headline(
        config: SelfDriverConfig,
        current_iteration: SelfDrivingTaskIteration,
        iteration_to_modify: SelfDrivingTaskIteration
):
    iteration_to_modify_str = f"Modifying iteration {iteration_to_modify.id} (v{iteration_to_modify.version_number})" if iteration_to_modify else "initial version of code"
    iteration_count = config.self_driving_task.selfdrivingtaskiteration_set.count()
    headline = f"""
--------------------------------------------------
{timezone.now().strftime("%m/%d/%Y %H:%M:%S")}

Task id {config.task.id} 
iteration id {current_iteration.id} (v{iteration_count})
{iteration_to_modify_str}
sandbox root dir: {os.path.abspath(config.sandbox_root_dir)}  
total spend: ${config.self_driving_task.get_cost() :.2f}/${config.budget :.2f}
tail -f {os.path.abspath(config.log_path)}

https://www.youtube.com/watch?v=-Ca-2FRsTx8&t=281s
--------------------------------------------------
                                    """
    print(headline)
    log(config, headline)


def log_execution_only_headline(config: SelfDriverConfig, current_iteration: SelfDrivingTaskIteration):
    iteration_count = config.self_driving_task.selfdrivingtaskiteration_set.count()
    headline = f"""
--------------------------------------------------
{timezone.now().strftime("%m/%d/%Y %H:%M:%S")}

Task id {config.task.id} 
sandbox root dir: {os.path.abspath(config.sandbox_root_dir)}  
total spend: ${config.self_driving_task.get_cost() :.2f}/${config.budget :.2f}

No coding - just gonna execute iteration {current_iteration.id} (v{current_iteration.version_number})
tail -f {os.path.abspath(config.log_path)}

https://www.youtube.com/watch?v=-Ca-2FRsTx8&t=281s
--------------------------------------------------
                                    """
    print(headline)
    log(config, headline)


def log_initial_test_only_headline(config: SelfDriverConfig, current_iteration: SelfDrivingTaskIteration):
    iteration_count = config.self_driving_task.selfdrivingtaskiteration_set.count()
    headline = f"""
--------------------------------------------------
{timezone.now().strftime("%m/%d/%Y %H:%M:%S")}

Task id {config.task.id} 
sandbox root dir: {os.path.abspath(config.sandbox_root_dir)}  
total spend: ${config.self_driving_task.get_cost() :.2f}/${config.budget :.2f}

First execution - will write the test for test driven development
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


def build_docker_image(
        current_iteration: SelfDrivingTaskIteration,
        envinronment: AwsEnv,
        docker_file: Path,
        log_f
) -> str:
    self_driving_task = current_iteration.self_driving_task
    
    docker_image_tag = sanitize_aws_name([
        self_driving_task.business.name,
        self_driving_task.id,
        current_iteration.version_number
    ], max_length=128)
    
    log_f.write(f"\n\n\n\n======== Begining DOCKER Build for tag {docker_image_tag} ")
    sandbox_path = self_driving_task.sandbox_path
    
    env = os.environ.copy()
    env["DOCKER_BUILDKIT"] = "1"
    
    build_process = subprocess.Popen(
        common.strings([
            "docker",
            "build",
            "--secret", "id=github_token,env=GITHUB_TOKEN",
            "-t", docker_image_tag,
            "-f", docker_file,
            docker_file.parent
        ]),
        stdout=log_f,
        stderr=subprocess.STDOUT,
        text=True,
        env=env
    )
    
    while build_process.poll() is None:
        time.sleep(1)
    
    if build_process.returncode != 0:
        raise Exception(f"Docker build failed with return code: {build_process.returncode}")
    
    log_f.write(f"======== COMPLETED DOCKER Build for {docker_image_tag}\n\n\n\n")
    
    return docker_image_tag


def push_image_to_ecr(
        iteration: SelfDrivingTaskIteration,
        envinronment: AwsEnv,
        docker_image_tag: str,
        log_f
):
    region = envinronment.get_aws_region()
    ecr_client = boto3.client("ecr", region_name=region)
    account_id = boto3.client("sts").get_caller_identity()["Account"]
    
    repo_name = sanitize_aws_name(iteration.self_driving_task.business.service_token)
    ecr_repo_uri = f"{account_id}.dkr.ecr.{region}.amazonaws.com/{repo_name}"
    
    full_image_uri = f"{ecr_repo_uri}:{docker_image_tag}"
    log_f.write(f"\n\n\n\n======== Begining ECR Push to {full_image_uri} ")
    log_f.flush()  # Ensure ECR auth logs are visible to tailing thread
    
    try:
        ecr_client.describe_repositories(repositoryNames=[repo_name])
    except ecr_client.exceptions.RepositoryNotFoundException:
        ecr_client.create_repository(repositoryName=repo_name)
    
    repo_desc = ecr_client.describe_repositories(repositoryNames=[repo_name])
    ecr_arn = repo_desc["repositories"][0]["repositoryArn"]
    
    subprocess.run(
        ["docker", "tag", docker_image_tag, full_image_uri],
        check=True,
        stdout=log_f,
        stderr=subprocess.STDOUT
    )
    
    env = os.environ.copy()
    env.pop("HTTP_PROXY", None)
    env.pop("http_proxy", None)
    env.pop("HTTPS_PROXY", None)
    env.pop("https_proxy", None)
    subprocess.run(
        ["docker", "push", full_image_uri],
        check=True,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        env=env
    )
    log_f.write(f"======== COMPLETED ECR Push to {full_image_uri}\n\n\n\n")
    
    return full_image_uri, ecr_arn


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


def execute_iteration(config: SelfDriverConfig, iteration: SelfDrivingTaskIteration, environment: AwsEnv) -> str:
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
            stop_tailing = start_log_tail_thread(logfile)
            
            try:
                docker_file = config.sandbox_root_dir / "Dockerfile"
                aws_utils.ecr_authenticate_for_dockerfile(
                    docker_file,
                    log_f
                )
                log_f.flush()  # Ensure ECR auth logs are visible to tailing thread
                
                docker_image_tag = build_docker_image(
                    iteration,
                    environment,
                    docker_file,
                    log_f
                )
                log_f.flush()  # Ensure Docker build logs are visible to tailing thread
                
                try:
                    full_image_uri, ecr_arn = push_image_to_ecr(
                        iteration,
                        environment,
                        docker_image_tag,
                        log_f
                    )
                except Exception as e:
                    raise AgentBlocked(f"task {task.id} is failing to push {docker_image_tag} to ECR. {e}")
                finally:
                    log_f.flush()  # Ensure ECR push logs are visible to tailing thread
                
                deploy_cloudformation_stacks(
                    config,
                    environment,
                    ecr_arn,
                    log_f
                )
                log_f.flush()  # Ensure CloudFormation deployment logs are visible to tailing thread
                
                if TaskType.CODING_ML.eq(task_type):
                    run_docker_command(
                        command_args=self_driving_task.main_name,
                        iteration=iteration,
                        running_process=running_process,
                        docker_image=docker_image_tag,
                        log_f=log_f
                    )
                    log_f.flush()  # Ensure ML execution logs are visible to tailing thread
                else:
                    if not AwsEnv.PRODUCTION.eq(environment):
                        run_docker_command(
                            command_args="test",
                            iteration=iteration,
                            running_process=running_process,
                            docker_image=docker_image_tag,
                            log_f=log_f
                        )
                        log_f.flush()  # Ensure test execution logs are visible to tailing thread
                    
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
                            docker_image=docker_image_tag,
                            log_f=log_f
                        )
                        log_f.flush()  # Ensure task execution logs are visible to tailing thread
                
                running_process.update_log_tail()
                running_process.is_running = False
                running_process.terminated_at = common.get_now()
                running_process.save(update_fields=['is_running', 'terminated_at'])
            finally:
                # Stop the tailing thread
                stop_tailing.set()
        
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
        log(config, log_output, f"iteration-{iteration.id}")
    finally:
        # Ensure tailing thread is stopped if it exists
        if 'stop_tailing' in locals():
            stop_tailing.set()
        
        config.business.snapshot_code(iteration, include_erie_common=False)
        with transaction.atomic():
            SelfDrivingTaskIteration.objects.filter(id=iteration.id).update(
                log_content_execution=common.truncate_text_lines(logfile.read_text())
            )
        common.quietly_delete(logfile)
        subprocess.run(["docker", "system", "prune", "-f"], check=True)
        print("PRUNING COMPLETE")


def start_log_tail_thread(logfile):
    # Start a background thread to tail the logfile contents to logging.info()
    import threading
    stop_tailing = threading.Event()
    
    def tail_logfile():
        """Tail the logfile and stream new content to logging.info()"""
        try:
            last_position = 0
            while not stop_tailing.is_set():
                try:
                    # Check if file exists and get its current size
                    if logfile.exists():
                        current_size = logfile.stat().st_size
                        if current_size > last_position:
                            # File has grown, read new content
                            with open(logfile, "r") as tail_f:
                                tail_f.seek(last_position)
                                new_content = tail_f.read(current_size - last_position)
                                if new_content:
                                    # Split into lines and log each one
                                    for line in new_content.splitlines():
                                        if line.strip():  # Only log non-empty lines
                                            logging.info(f"[Docker Execution] {line}")
                            last_position = current_size
                    
                    # Wait before checking again
                    time.sleep(0.2)
                except (FileNotFoundError, OSError):
                    # File might not exist yet or be temporarily unavailable
                    time.sleep(0.5)
        except Exception as e:
            logging.error(f"Error tailing logfile: {e}")
    
    tail_thread = threading.Thread(target=tail_logfile, daemon=True)
    tail_thread.start()
    return stop_tailing


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


def evaluate_iteration_execution(config: SelfDriverConfig, iteration: SelfDrivingTaskIteration):
    iteration: SelfDrivingTaskIteration = SelfDrivingTaskIteration.objects.get(id=iteration.id)
    
    eval_data = llm_chat(
        "Iteration Summarizer",
        config,
        [
            get_sys_prompt("iteration_summarizer.md"),
            LlmMessage.user(f"""
**Logs from iteration test output and execution**
Base your evaluation on this log output
========= BEGIN LOG OUTPUT =======================
{iteration.log_content_execution}
========= END LOG OUTPUT =======================""")
        ],
        config.model_iteration_evaluation,
        output_schema=PROMPTS_DIR / "iteration_summarizer.md.schema.json"
    ).json()
    
    with transaction.atomic():
        SelfDrivingTaskIteration.objects.filter(id=iteration.id).update(
            evaluation_json=eval_data
        )
    
    previous_iteration_evals = []
    for prev_iter in config.self_driving_task.selfdrivingtaskiteration_set.filter(
            evaluation_json__isnull=False
    ).order_by("-timestamp")[:20][::-1]:
        previous_iteration_evals.append(LlmMessage.user(f"""
**Iteration Evaluation**
{"This is the evaluation of the most iteration current iteration - this reflects the latest state of the code" if prev_iter == iteration else ""}
iteration_evaluator output for iteration_id = '{prev_iter.id}'
iteration timestamp: {prev_iter.timestamp}
========= BEGIN iteration_id = '{prev_iter.id}' EVALUATION =======================
{json.dumps(prev_iter.evaluation_json, indent=4)}
========= END iteration_id = '{prev_iter.id}' EVALUATION =======================
"""))
    
    selection_data = llm_chat(
        "Iteration Selector",
        config,
        [
            get_sys_prompt("iteration_selector.md"),
            *previous_iteration_evals
        ],
        config.model_iteration_evaluation,
        output_schema=PROMPTS_DIR / "iteration_selector.md.schema.json"
    ).json()
    
    with transaction.atomic():
        SelfDrivingTaskIteration.objects.filter(id=iteration.id).update(
            evaluation_json={
                **selection_data,
                **eval_data
            }
        )
    
    iteration.refresh_from_db(fields=["evaluation_json"])
    eval_data = iteration.evaluation_json
    
    if iteration.goal_achieved():
        raise GoalAchieved(eval_data)
    
    return eval_data


def build_previous_iteration_context_messages(
        config: SelfDriverConfig,
        current_iteration: SelfDrivingTaskIteration,
        previous_iteration: SelfDrivingTaskIteration,
        iteration_to_modify: SelfDrivingTaskIteration
) -> List[LlmMessage]:
    eval_json = config.self_driving_task.get_most_recent_iteration().evaluation_json or {}
    
    previous_iteration_count = eval_json.get("previous_iteration_count", 1)
    all_iterations = list(
        config.self_driving_task.selfdrivingtaskiteration_set.exclude(
            id=current_iteration.id
        ).filter(
            evaluation_json__isnull=False
        ).order_by("timestamp")
    )
    
    previous_iterations = all_iterations[-previous_iteration_count:]
    
    if iteration_to_modify and iteration_to_modify not in previous_iterations:
        previous_iterations.append(iteration_to_modify)
    
    if previous_iteration and previous_iteration not in previous_iterations:
        previous_iterations.append(previous_iteration)
    
    messages = get_iteration_eval_llm_messages(
        previous_iterations,
        previous_iteration,
        iteration_to_modify
    )
    
    # if previous_iteration and previous_iteration != iteration_to_modify:
    #     messages.append(LlmMessage.user(f"""
    # **Log Output**
    # Log output from executing the PREVIOUS ITERATION (iteration_id = '{iteration_to_modify.id})'
    # ========= BEGIN Log Output =======================
    # {iteration_to_modify.log_content_execution}
    # ========= END Log Output =======================
    #             """))
    
    # if iteration_to_modify:
    #     messages.append(LlmMessage.user(f"""
    # **Log Output**
    # Log output from executing the ITERATION WE ARE ROLLING THE CODE BACK TO (iteration_id = '{iteration_to_modify.id})'
    # ========= BEGIN Log Output =======================
    # {iteration_to_modify.log_content_execution}
    # ========= END Log Output =======================
    #         """))
    
    return messages


def get_iteration_eval_llm_messages(
        iterations: list[SelfDrivingTaskIteration],
        previous_iteration: SelfDrivingTaskIteration = None,
        iteration_to_modify: SelfDrivingTaskIteration = None
) -> list[LlmMessage]:
    return [
        LlmMessage.user(f"""
**iteration_evaluator Output**
{"This is the evalutation of the execution of the previous iteration of the code" if iteration == previous_iteration and previous_iteration != iteration_to_modify else ""}
{"We are rolling the code back to this iteration. This is the evalutation of the execution of iteration of the code we are rolling back to.  We will start our new changes from this code" if iteration == iteration_to_modify else ""}
iteration_evaluator output for iteration_id = '{iteration.id}'
iteration timestamp: {iteration.timestamp}
========= BEGIN iteration_id = '{iteration.id}' EVALUATION =======================
{
        json.dumps(
            {
                "evaluation": iteration.evaluation_json.get("evaluation", "none"),
                "strategic_guidance": iteration.evaluation_json.get("strategic_guidance", "none"),
                
            },
            indent=4
        )
        }
========= END iteration_id = '{iteration.id}' EVALUATION =======================
        """)
        for iteration in sorted(iterations, key=lambda i: i.timestamp)
    ]


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


def generate_code(
        config: SelfDriverConfig,
        current_iteration: SelfDrivingTaskIteration,
        previous_iteration: SelfDrivingTaskIteration,
        iteration_to_modify: SelfDrivingTaskIteration,
        planning_data: dict
) -> SelfDrivingTaskIteration:
    code_file_instructions = planning_data.get("code_files", [])
    if not code_file_instructions:
        raise BadPlan("no code files found", planning_data)
    
    code_file_instructions = (
            [cfi for cfi in code_file_instructions if cfi.get("code_file_path") == "requirements.txt"]
            +
            [cfi for cfi in code_file_instructions if cfi.get("code_file_path") != "requirements.txt"]
    )
    
    if previous_iteration and (previous_iteration != iteration_to_modify):
        roll_back_reason = planning_data.get("reason_for_iteration_id_to_modify")
    else:
        roll_back_reason = None
    
    requirements_txt = CodeFile.get(config, "requirements.txt").get_latest_version().code
    
    for cfi in code_file_instructions:
        code_file_path_str: str = cfi.get("code_file_path")
        if code_file_path_str.startswith("/"):
            raise BadPlan(f"invalid file path: {code_file_path_str} - code file paths are forbidden from starting with a slash", planning_data)
        
        if code_file_path_str.startswith(str(config.sandbox_root_dir)):
            code_file_path_str = code_file_path_str[len(str(config.sandbox_root_dir)) + 1:]
        
        code_file_path: Path = config.sandbox_root_dir / code_file_path_str
        if not code_file_path:
            raise BadPlan(f"missing code file name: {json.dumps(cfi)}", planning_data)
        
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
                current_iteration,
                code_version_to_modify.code
            )
        else:
            previous_exception = None
            code_str = None
            for i in range(3):
                try:
                    previous_exception = None
                    code_str = write_code(
                        config=config,
                        code_version_to_modify=code_version_to_modify,
                        instructions=instructions,
                        requirements_txt=requirements_txt,
                        code_writing_model=LlmModel(cfi.get("code_writing_model")),
                        current_iteration=current_iteration,
                        previous_iteration=previous_iteration,
                        iteration_to_modify=iteration_to_modify,
                        roll_back_reason=roll_back_reason,
                        previous_exception=previous_exception
                    )
                    
                    break
                except CodeCompilationError as e:
                    previous_exception = e
            
            if previous_exception:
                # validation failed three times.  keep going, if it fails in deployment or execution we'll have another 
                # chances at the feedback loop
                logging.exception(previous_exception)
            
            if code_str:
                code_file.update(
                    current_iteration,
                    code_str,
                    code_instructions=instructions
                )
                if code_file_path_str == "requirements.txt":
                    requirements_txt = code_str
    
    return current_iteration


def write_initial_test(
        config: SelfDriverConfig,
        current_iteration: SelfDrivingTaskIteration
):
    task = config.task
    
    previous_exception = None
    
    for i in range(3):
        try:
            messages = [
                get_sys_prompt("codewriter--initial_test.md"),
                # Insert strict Python-only output message at the top:
                LlmMessage.user("Please output only valid Python source code. Do not include Markdown formatting, triple backticks, or explanatory comments. The output must be a single Python file that can be executed directly."),
                LlmMessage.user(f'''
**Please write a single file, comprensive test suite that asserts this behavior.  This test suite will be used for Test Driven Development**

# Goal
{task.description}

# Test Plan
{task.test_plan or 'none'}

# Risk Notes
{task.risk_notes or 'none'}
        ''')
            ]
            
            if previous_exception:
                messages.append(LlmMessage.user(f"""
    Your previous attempt at writing this code failed with this exception:
    {previous_exception}

    Please attempt to write the code again and avoid causing this error
                """))
            
            code = llm_chat(
                "Write initial test",
                config,
                messages,
                LlmModel.CLAUDE_3_OPUS_DO_NOT_USE_VERY_EXPENSIVE
            ).text
            
            test_file_path_dir = config.sandbox_root_dir / "core" / "tests"
            test_file_path_dir.mkdir(parents=True, exist_ok=True)
            (test_file_path_dir / "__init__.py").touch(exist_ok=True)
            test_file_path = test_file_path_dir / common.sanitize_filename(f'test_{task.id}.py')
            
            for code_validation_idx in range(5):
                try:
                    if "from django.test import TestCase" not in code:
                        raise CodeCompilationError(code, f"The tests **MUST** extend from \n'''\nfrom django.test import TestCase\n'''")
                    
                    validate_code(
                        test_file_path,
                        code
                    )
                    break
                except CodeCompilationError as code_compilation_error:
                    logging.warning(f"Code failed validation. Attempting fix using cheaper model.  Fix attempt {code_validation_idx + 1} of 5")
                    if code_validation_idx == 5:
                        raise code_compilation_error
                    else:
                        code = fix_code_compilation(
                            config,
                            test_file_path,
                            code,
                            code_compilation_error
                        )
            
            test_file_path.write_text(code)
            
            with transaction.atomic():
                code_verson = current_iteration.get_code_version(test_file_path)
                SelfDrivingTask.objects.filter(id=config.self_driving_task.id).update(
                    test_file_path=test_file_path.relative_to(config.sandbox_root_dir)
                )
                config.self_driving_task.refresh_from_db(fields=["test_file_path"])
            
            return test_file_path
        except Exception as e:
            logging.exception(e)
            previous_exception = e
    
    raise previous_exception


def write_initial_design(
        config: SelfDriverConfig,
        current_iteration: SelfDrivingTaskIteration
):
    task = config.task
    
    try:
        messages = [
            get_sys_prompt("codewriter--initial_design.md"),
            LlmMessage.user(f'''
        **Please write a markdown-formatted high-level design document for this task.**

        This document will serve as the roadmap for the code planner and future iterations. 
        Do not write implementation code or tests. Focus on describing architecture, assumptions, data flow, interfaces, and potential risks.

        # Goal
        {task.description}

        # Acceptance Criteria
        {task.test_plan or 'none'}

        # Risk Notes
        {task.risk_notes or 'none'}
            ''')
        ]
        
        code = llm_chat(
            "Write initial design doc",
            config,
            messages,
            LlmModel.CLAUDE_3_OPUS_DO_NOT_USE_VERY_EXPENSIVE
        ).text
        
        design_file_path = update_file_contents(
            config,
            current_iteration,
            code
        )
        
        SelfDrivingTask.objects.filter(id=config.self_driving_task.id).update(
            design_doc_path=design_file_path.relative_to(config.sandbox_root_dir)
        )
        config.self_driving_task.refresh_from_db(fields=["design_doc_path"])
        
        return design_file_path
    except Exception as e:
        logging.exception(e)
        previous_exception = e


def update_file_contents(config, current_iteration, code):
    docs_dir = config.sandbox_root_dir / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    test_file_path = docs_dir / "architecture.md"
    test_file_path.write_text(code)
    with transaction.atomic():
        code_verson = current_iteration.get_code_version(test_file_path)
    return test_file_path


def get_budget_message(config) -> LlmMessage:
    iteration_count = config.self_driving_task.selfdrivingtaskiteration_set.count()
    
    return LlmMessage.user(f"""
## Budget Information
This is your attempt number {iteration_count + 1} on this Task

You've spent ${config.self_driving_task.get_cost() :.2f} USD out of a max budget of ${config.budget :.2f} USD
    """)


def plan_code_changes(
        config: SelfDriverConfig,
        current_iteration: SelfDrivingTaskIteration,
        previous_iteration: SelfDrivingTaskIteration,
        iteration_to_modify: SelfDrivingTaskIteration,
        relevant_code_files: list[LlmMessage]
):
    model = config.model_code_planning
    business = config.self_driving_task.business
    
    task = config.self_driving_task.task
    task_type = TaskType(task.task_type)
    
    system_prompt_files = [
        "codeplanner--base.md",
        MAP_TASKTYPE_TO_PLANNING_PROMPT[task_type]
    ]
    
    if config.self_driving_task.test_file_path:
        system_prompt_files.append(
            "codeplanner--test_driven_development.md"
        )
    
    messages = [
        *common.ensure_list(get_sys_prompt(
            system_prompt_files,
            [
                ("<test_file_path>", str(config.self_driving_task.test_file_path or "")),
                ("<aws_tag>", str(business.service_token)),
                ("<db_name>", str(business.service_token)),
                ("<iam_role_name>", str(business.get_iam_role_name())),
                ("<artifacts_directory>", str(config.artifacts_dir)),
                ("<sandbox_dir>", str(config.sandbox_root_dir))
            ]
        )),
        *common.ensure_list(
            get_budget_message(config)
        ),
        *common.ensure_list(
            build_previous_iteration_context_messages(
                config,
                current_iteration,
                previous_iteration,
                iteration_to_modify
            )
        ),
        *common.ensure_list(
            get_dependencies_msg(config, for_planning=True)
        ),
        *common.ensure_list(
            relevant_code_files
        ),
        *common.ensure_list(
            get_docs_msg(config)
        ),
        *common.ensure_list(
            get_file_structure_msg(config.sandbox_root_dir) if not iteration_to_modify.deployment_failed() else []
        ),
        *common.ensure_list(
            config.guidance
        ),
        *common.ensure_list(
            LlmMessage.user(f"""
The previous iteration failed at the deployment stage.   

**Application level code changes are FORBIDDEN at this point, and will be FORBIDDEN until the deployment is fixed**
- Any application level code changes at this point would be purely speculative and not based on an execution feedback loop
- You may only plan changes for environment /  infrastructure files (Dockerfile, cloudformation configs (infrastructure.yaml), requirements.txt, etc)

**YOUR PRIMARY OBJECTIVE AT THIS POINT IS TO FIX THE DEPLOYMENT PROBLEM**
            """)
            if iteration_to_modify.deployment_failed() else get_goal_msg(config)
        )
    ]
    
    planning_data = llm_chat(
        "Plan code changes",
        config,
        messages,
        model,
        debug=True,
        output_schema=PROMPTS_DIR / "codeplanner.schema.json"
    ).json()
    
    with transaction.atomic():
        SelfDrivingTaskIteration.objects.filter(id=current_iteration.id).update(
            planning_model=model,
            execute_module=planning_data.get('execute_module'),
            test_module=planning_data.get('test_module')
        )
        current_iteration.refresh_from_db(fields=["planning_model", "execute_module", "test_module"])
    
    blocked_data = planning_data.get('blocked')
    if blocked_data:
        raise AgentBlocked(blocked_data)
    
    return planning_data


def write_code(
        config: SelfDriverConfig,
        code_version_to_modify: CodeVersion,
        instructions,
        code_writing_model: LlmModel,
        requirements_txt: str,
        current_iteration: SelfDrivingTaskIteration,
        previous_iteration: SelfDrivingTaskIteration = None,
        iteration_to_modify: SelfDrivingTaskIteration = None,
        roll_back_reason: str = None,
        previous_exception: Optional[CodeCompilationError] = None
) -> str:
    code_file = code_version_to_modify.code_file
    code_file_path = code_file.get_path()
    code_file_path = code_file_path
    code_file_name = code_file_path.name
    
    prompt = get_codewriter_system_prompt(code_file_path)
    
    messages: list[LlmMessage] = [
        *common.ensure_list(
            get_sys_prompt(
                prompt,
                ("<sandbox_dir>", str(config.sandbox_root_dir))
            ))
    ]
    
    if code_file_name.endswith(".py"):
        LlmMessage.user(
            f"""
The python environment has the following packages installed.  The code you write may only import from packages listed here
========== begin requirements.txt ================
{requirements_txt}
========== end requirements.txt ================
                """
        )
    
    if previous_iteration:
        previous_iteration_version = code_file.get_version(previous_iteration)
        if previous_iteration_version:
            prev_diff = previous_iteration_version.get_diff()
            if prev_diff:
                messages.append(
                    LlmMessage.user(
                        f"### PREVIOUS ITERATION DIFF for {code_file_path}:\n{prev_diff}"
                    )
                )
    
    if iteration_to_modify and iteration_to_modify != previous_iteration:
        # we are rolling back to a version that's not the previous version
        code_version_to_modify_diff = code_version_to_modify.get_diff()
        if code_version_to_modify_diff:
            messages.append(
                LlmMessage.user(
                    f"### ROLLED-BACK ITERATION DIFF for {code_file_path} {roll_back_reason if roll_back_reason else ''}:\n{code_version_to_modify_diff}"
                )
            )
    
    code_file_path = code_file.get_path()
    if code_version_to_modify.code:
        print("modifying", code_file_path)
        
        messages.append(
            LlmMessage.user(
                f"iteration id={str(code_version_to_modify.task_iteration.id)} code_file_path {code_file_path}",
                config.sandbox_root_dir / code_file.file_path
            )
        )
        
        messages.append(
            LlmMessage.user(
                f"""
**YOUR ONE AND ONLY TASK:**
Modify {code_file_path}, following each of these instructions exactly and in order, while avoiding repeating any previous erros (if applicable):

{json.dumps(instructions, indent=4)}
    """
            )
        )
    
    else:
        messages.append(
            LlmMessage.user(
                f"""
**YOUR ONE AND ONLY TASK:**
Write the initial version of {code_file_path}, following each of these instructions exactly and in order:

{json.dumps(instructions, indent=4)}
        """
            )
        )
    
    if previous_exception:
        messages.append(LlmMessage.user(f'''
This is code from your previous attempt to generate code based on the instruction set:
============ BEGIN PREVIOUS CODE =============
{previous_exception.code_str}
============ END PREVIOUS CODE ===============

This code failed the validation step with the following error(s): 
============ BEGIN VALIDATION EXCEPTION =============
{str(previous_exception)}
============ END VALIDATION EXCEPTION ===============

Please try writing the code again and avoid these errors
           '''))
    
    code = llm_chat(
        f"Write code for {code_file_name}",
        config,
        messages,
        code_writing_model,
        debug=True
    ).text
    
    code_compilation_error = None
    for i in range(5):
        try:
            return validate_code(
                code_file_path,
                code
            )
        except CodeCompilationError as code_compilation_error:
            logging.warning(f"Primary code failed validation. Attempting fix using cheaper model.  Fix attempt {i + 1} of 5")
            code = fix_code_compilation(
                config,
                code_file_path,
                code,
                code_compilation_error
            )
    
    raise code_compilation_error


def fix_code_compilation(config, code_file_path, code, e):
    code = llm_chat(
        "Fix compilation error cheaply",
        config,
        [
            LlmMessage.sys("You are a code fixer. Your job is to fix syntax or compilation errors in a code file."),
            LlmMessage.user(f"""
This is the code (from file {code_file_path}) that failed validation:
```
{code}
```

Here is the exception message:
{str(e)}

Please return only the corrected version of the code. No explanation, no formatting.
""")
        ],
        model=LlmModel.OPENAI_GPT_3_5_TURBO,
        code_response=True
    ).text
    return code


def get_codewriter_system_prompt(code_file_path):
    code_file_name = code_file_path.name
    code_file_name_lower = code_file_name.lower()
    if code_file_name_lower in ["requirements.txt", "constraints.txt"]:
        prompt = "codewriter--requirements.txt.md"
    elif code_file_name_lower.endswith(".json"):
        prompt = "codewriter--json_coder.md"
    elif code_file_name_lower.endswith(".eml"):
        prompt = "codewriter--eml_coder.md"
    elif code_file_name_lower.endswith(".md"):
        prompt = "codewriter--documentation_writer.md"
    elif code_file_name == "infrastructure.yaml":
        prompt = "codewriter--aws_cloudformation_coder.md"
    elif code_file_name.startswith("Dockerfile"):
        prompt = "codewriter--dockerfile_coder.md"
    elif code_file_name_lower.endswith(".py"):
        prompt = "codewriter--python_coder.md"
    elif code_file_name_lower.endswith(".sql"):
        prompt = "codewriter--sql_coder.md"
    elif code_file_name_lower.endswith(".js"):
        prompt = "codewriter--javascript_coder.md"
    elif code_file_name_lower.endswith(".html"):
        prompt = "codewriter--html_coder.md"
    elif code_file_name_lower.endswith(".css"):
        prompt = "codewriter--css_coder.md"
    else:
        raise AgentBlocked(f"no coder implemented for {code_file_name}.  Need JJ or a human to implement it in the Erie Iron agent codebase.")
    return prompt


def validate_code(code_file_path: Path, code: str) -> str:
    code_file_name = code_file_path.name.lower()
    if code_file_name.endswith(".js"):
        import subprocess
        import tempfile
        try:
            with tempfile.NamedTemporaryFile(mode='w+', suffix=".js", delete=False) as tmp:
                tmp.write(code)
                tmp.flush()
                result = subprocess.run(
                    ["eslint", "--no-eslintrc", "--stdin", "--stdin-filename", tmp.name],
                    capture_output=True,
                    text=True
                )
            if result.returncode != 0:
                raise CodeCompilationError(code, f"JavaScript lint errors:\n{result.stdout.strip()}")
        finally:
            os.remove(tmp.name)
    
    elif code_file_name.endswith(".css"):
        import subprocess
        import tempfile
        try:
            with tempfile.NamedTemporaryFile(mode='w+', suffix=".css", delete=False) as tmp:
                tmp.write(code)
                tmp.flush()
                result = subprocess.run(
                    ["stylelint", tmp.name],
                    capture_output=True,
                    text=True
                )
            if result.returncode != 0:
                raise CodeCompilationError(code, f"CSS lint errors:\n{result.stdout.strip()}")
        finally:
            os.remove(tmp.name)
    
    elif code_file_name.endswith("json"):
        try:
            json.loads(code)
        except Exception as e:
            raise CodeCompilationError(code, f"json parse error:\n{e}")
    
    elif code_file_name.startswith("Dockerfile"):
        import subprocess
        import tempfile
        try:
            with tempfile.NamedTemporaryFile(mode='w+', suffix="", delete=False) as tmp:
                tmp.write(code)
                tmp.flush()
                result = subprocess.run(
                    ["hadolint", tmp.name],
                    capture_output=True,
                    text=True
                )
            if result.returncode != 0:
                raise CodeCompilationError(code, f"Dockerfile lint errors:\n{result.stdout.strip()}")
        finally:
            os.remove(tmp.name)
    
    elif False and code_file_name == "infrastructure.yaml":
        ## skipping this for now, as it seems like it's better for the feedback look to let cloudformation surface the errors
        import subprocess
        import tempfile
        import json as _json
        try:
            with tempfile.NamedTemporaryFile(mode='w+', suffix=".yaml", delete=False) as tmp:
                tmp.write(code)
                tmp.flush()
                result = subprocess.run(
                    ["cfn-lint", "--format", "json", tmp.name],
                    capture_output=True,
                    text=True
                )
            if result.returncode != 0:
                try:
                    findings = _json.loads(result.stdout)
                    errors_only = [f for f in findings if f.get("Level") == "Error"]
                    if errors_only:
                        error_msgs = "\n".join(f"Line {f['Location']['Start']['LineNumber']}: {f['Message']}" for f in errors_only)
                        raise CodeCompilationError(code, f"CloudFormation lint errors:\n{error_msgs}")
                except Exception as cf_lint_e:
                    logging.exception(cf_lint_e)
                    raise CodeCompilationError(code, f"CloudFormation lint errors:\n{result.stdout.strip()}")
        finally:
            os.remove(tmp.name)
    
    elif code_file_name.endswith(".py"):
        import ast
        try:
            ast.parse(code)
        except SyntaxError as e:
            raise CodeCompilationError(code, f"Syntax error in Python file '{code_file_name}': {e}")
    elif code_file_name == "requirements.txt":
        # noinspection PyProtectedMember
        from pip._internal.req.constructors import install_req_from_line
        # noinspection PyProtectedMember
        from pip._internal.exceptions import InstallationError
        
        lines = code.splitlines()
        for i, line in enumerate(lines, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue  # Allow empty lines and comments
            try:
                install_req_from_line(line)
            except InstallationError as e:
                raise CodeCompilationError(line, f"Invalid requirement on line {i}: '{line}' — {e}")
    
    return code


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


def get_docs_msg(config) -> list[LlmMessage]:
    def _load_markdown_doc(path: Path) -> Optional[LlmMessage]:
        if path.exists() and path.is_file():
            return None
    
    files = []
    # readme_path = config.sandbox_root_dir / "README.md"
    # if readme_path.exists():
    #     files.append(readme_path)
    
    docs_dir = config.sandbox_root_dir / "docs"
    if docs_dir.exists():
        for md_file in docs_dir.glob("*.md"):
            files.append(md_file)
    
    return [
        LlmMessage.user(f"""
The following is the content of `./docs/{file.name}`, a Markdown documentation file. 
It may contain high-level design notes, architecture explanations, or usage instructions useful to developers and future planning agents.

{file.read_text()}
        """)
        for file in files
    ]


def get_goal_msg(config):
    task = config.self_driving_task.task
    return LlmMessage.user(f'''
Please plan code changes that work towards achieving this GOAL:

# Goal
{task.description}

# Test Plan
{task.test_plan or 'none'}

# Risk Notes
{task.risk_notes or 'none'}

# PRIMARY OBJECTIVE
**ACHIEVING THIS GOAL IS YOUR PRIMARY OBJECTIVE**
''')


def get_cloudformation_file(config: SelfDriverConfig) -> Path:
    return common.assert_exists(config.sandbox_root_dir / "infrastructure.yaml")


def get_relevant_code_files(
        config: SelfDriverConfig,
        current_iteration: SelfDrivingTaskIteration,
        iteration_to_modify: SelfDrivingTaskIteration
) -> list[LlmMessage]:
    messages = []
    
    ## deployment failed - just return deployment files
    if iteration_to_modify.deployment_failed():
        deployment_files: list[Path] = [
            config.sandbox_root_dir / "Dockerfile",
            config.sandbox_root_dir / "infrastructure.yaml",
            config.sandbox_root_dir / "requirements.txt"
        ]
        
        for f in deployment_files:
            try:
                relative_file = f.relative_to(config.sandbox_root_dir)
            except:
                relative_file = f
            
            code_file = CodeFile.get(
                config.business,
                relative_file
            )
            
            code_version = code_file.get_version(iteration_to_modify, default_to_latest=True)
            if not code_version:
                code_version = CodeFile.init_from_codefile(current_iteration, relative_file)
            
            if code_version and code_version.code:
                messages.append(code_version.get_llm_message())
            else:
                print(f"??? {f}")
        return messages
    
    required_files = [
        config.self_driving_task.test_file_path,
        config.self_driving_task.design_doc_path,
        "infrastructure.yaml",
        "requirements.txt"
    ]
    
    iteration_code_files = set()
    iteration_code_versions = []
    for f in common.filter_none(required_files):
        iteration_code_versions.append(
            CodeFile.get(
                business=config.business,
                code_file_path=f
            ).get_version_for_iteration(
                iteration_to_modify
            )
        )
    
    iteration_code_versions += list(CodeVersion.objects.filter(
        task_iteration=iteration_to_modify
    ).exclude(
        id__in=[cv.id for cv in iteration_code_versions]
    ))
    
    for code_version in iteration_code_versions:
        iteration_code_files.add(code_version.code_file_id)
        
        file_path = code_version.code_file.file_path
        code = code_version.code
        was_modified = code_version.task_iteration_id == iteration_to_modify.id
        
        if code:
            messages.append(LlmMessage.user({
                "file_path": file_path,
                "modified_in_previous_iteration": was_modified,
                "may_edit": True,
                "code": code,
            }))
    
    # Step 1: Get the structured retrieval cues from the LLM
    cues = llm_chat(
        "Find relavant code",
        config,
        [
            get_sys_prompt("codefinder.md"),
            LlmMessage.user(config.self_driving_task.task.get_work_desc())
        ],
        LlmModel.OPENAI_GPT_3_5_TURBO,
        output_schema=PROMPTS_DIR / "codefinder.md.schema.json",
        code_response=True
    ).json()
    
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
        .exclude(code_version__code_file__id__in=iteration_code_files)
        .annotate(
            similarity=RawSQL("erieiron_codemethod.codebert_embedding <-> %s::vector", [prompt_embedding])
        )
        .filter(similarity__lte=similarity_threshold)
        .order_by("similarity")
    )
    
    # Keep track of which code files we've already included to ensure only latest version per file
    processed_code_files = set()
    methods_by_file = defaultdict(list)
    for code_method in set(list(erie_common_code_methods) + list(additional_code_methods)):
        code_file = code_method.code_version.code_file
        if code_file.id in processed_code_files:
            continue
        methods_by_file[code_file].append(code_method)
    
    for code_file, methods in methods_by_file.items():
        processed_code_files.add(code_file.id)
        grouped_code = "\n\n".join([
            f"# Method: {method.name}\n{method.code}" for method in methods
        ])
        messages.append(LlmMessage.user({
            "file_path": code_file.file_path,
            "may_edit": False,
            "source": "imported_package",
            "code": grouped_code,
            "note": f"The following methods from an imported package may be referenced but not modified: {[m.name for m in methods]}"
        }))
    
    # Then get code versions for other file types (excluding Python and JavaScript)
    # Also exclude files that are already included from previous iteration or code methods above
    all_excluded_code_files = set(iteration_code_files) | processed_code_files
    
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
        if code_version.code:
            messages.append(code_version.get_llm_message())
    
    return messages


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


def llm_chat(
        desc: str,
        config: SelfDriverConfig,
        messages: list[LlmMessage],
        model: LlmModel,
        output_schema: Path = None,
        code_response=False,
        debug=False
) -> LlmResponse:
    token_count = LlmMessage.get_total_token_count(model, messages)
    max_tokens = MODEL_TO_MAX_TOKENS.get(model)
    
    if max_tokens:
        print(f"{desc}: about to call out to {model}. {token_count:,}/{max_tokens:,} tokens used")
    else:
        print(f"{desc}: about to call out to {model}.  {token_count:,} tokens used")
    
    log_llm_request(config, messages)
    llm_resp = llm_interface.chat(
        messages=messages,
        model=model,
        output_schema=output_schema,
        code_response=code_response,
        debug=debug
    )
    log_llm_response(config, llm_resp)
    
    print(f"{desc}: total ${llm_resp.price_total:.4f}; input ${llm_resp.price_input:.4f}; output ${llm_resp.price_output:.4f} - total spend is ${config.self_driving_task.get_cost() :.2f} out a budget of ${config.budget :.2f}")
    
    return llm_resp


def deploy_cloudformation_stacks(
        config: SelfDriverConfig,
        environment: AwsEnv,
        ecr_arn: str,
        log_f,
):
    cf_client = boto3.client("cloudformation", region_name=environment.get_aws_region())
    
    self_driving_task = config.self_driving_task
    sandbox_path = Path(config.sandbox_root_dir)
    
    cfn_file = get_cloudformation_file(config)
    stack_name = self_driving_task.get_cloudformation_stack_name(environment)
    log_f.write(f"\n\n\n\n======== Begining cloudformation deploy for {stack_name} ")
    
    start_time = time.time()
    try:
        
        aws_utils.prepare_stack_for_update(
            stack_name,
            cf_client
        )
        
        try:
            if aws_utils.get_stack(stack_name, cf_client):
                aws_utils.assert_cloudformation_stack_valid(
                    stack_name,
                    cf_client
                )
        except:
            raise AgentBlocked(f"cloudformation stack {stack_name} in {environment.get_aws_region()} is wedged and cannot be autonomously fixed.  JJ or a Human needs to clean up manually")
        
        cloudformation_params = get_stack_parameters(
            self_driving_task,
            environment,
            cfn_file,
            ecr_arn,
            log_f
        )
        
        aws_utils.push_cloudformation(
            stack_name,
            environment,
            cfn_file,
            cloudformation_params,
            log_f
        )
        log_f.write(f"======== COMPLETED cloudformation deploy for {stack_name}\n\n\n\n")
    except Exception as deploy_exception:
        log_f.write(f"Error deploying CloudFormation stack {stack_name}: {deploy_exception}\n")
        log_f.write(traceback.format_exc())
        try:
            events = cf_client.describe_stack_events(StackName=stack_name)["StackEvents"]
            # Filter events to only include those that occurred after deployment start_time
            from datetime import datetime, timezone
            deployment_start_datetime = datetime.fromtimestamp(start_time, tz=timezone.utc)
            recent_events = [
                e for e in events
                if e['Timestamp'] >= deployment_start_datetime
            ]
            # Sort events in ascending order (oldest first)
            recent_events.sort(key=lambda x: x['Timestamp'])
            
            failed_events = [
                f"{e['Timestamp']} | Stack: {e['StackName']} | {e['LogicalResourceId']} ({e['ResourceType']}) | Status: {e['ResourceStatus']} | Reason: {e.get('ResourceStatusReason', '')} | PhysicalId: {e.get('PhysicalResourceId', '')} | Token: {e.get('ClientRequestToken', '')}"
                for e in recent_events if "FAILED" in e["ResourceStatus"]
            ]
            log_f.write("CloudFormation failure events:\n")
            log_f.write("\n".join(failed_events) + "\n")
            
            # Also describe the top 3 failed resources in more detail
            failed_logical_ids = [e["LogicalResourceId"] for e in recent_events if "FAILED" in e["ResourceStatus"]]
            log_f.write("\nDetailed resource descriptions for top failures:\n")
            for logical_id in failed_logical_ids[:3]:
                try:
                    resource_details = cf_client.describe_stack_resource(
                        StackName=stack_name,
                        LogicalResourceId=logical_id
                    )
                    log_f.write(json.dumps(resource_details, indent=2, default=str) + "\n")
                except Exception as ex:
                    log_f.write(f"Failed to describe resource {logical_id}: {ex}\n")
        except Exception as event_ex:
            log_f.write(f"Failed to fetch stack events: {event_ex}\n")
        
        from datetime import datetime, timedelta
        log_f.write("\nRecent CloudTrail events in this region (last 15 min):\n")
        try:
            ct_client = boto3.client("cloudtrail", region_name=environment.get_aws_region())
            end_time = common.get_now()
            ct_query_start_time = end_time - timedelta(minutes=15)
            
            ct_events = ct_client.lookup_events(
                StartTime=ct_query_start_time,
                EndTime=end_time,
                MaxResults=100
            )
            
            # Filter events to only include those that occurred after deployment start_time
            from datetime import timezone
            deployment_start_datetime = datetime.fromtimestamp(start_time, tz=timezone.utc)
            recent_ct_events = [
                event for event in ct_events.get("Events", [])
                if event['EventTime'] >= deployment_start_datetime
            ]
            # Sort events in ascending order (oldest first)
            recent_ct_events.sort(key=lambda x: x['EventTime'])
            
            for ct_event in recent_ct_events:
                cloudtrain_event_data = json.loads(ct_event["CloudTrailEvent"])
                error_message: str = cloudtrain_event_data.get("errorMessage")
                if error_message:
                    if "No updates are to be performed" in error_message:
                        continue
                    
                    if error_message.lower().startswith("stack with id") and error_message.lower().endswith("does not exist"):
                        continue
                    
                    log_f.write(f'\n\n\nCloudTrail Event: {ct_event.get("EventName")} at {ct_event.get("EventTime")}\n')
                    log_f.write(json.dumps(cloudtrain_event_data, indent=4))
        
        except Exception as ct_ex:
            log_f.write(f"Failed to fetch CloudTrail events: {ct_ex}\n")
        
        raise deploy_exception


def get_stack_parameters(
        self_driving_task: SelfDrivingTask,
        environment: AwsEnv,
        cfn_file: Path,
        ecr_arn: str,
        log_f
):
    project_name = aws_utils.sanitize_aws_name(self_driving_task.business.service_token, max_length=64)
    secrets_key = f"/erieiron/{project_name}/{environment.value}"
    
    known_params = {
        "StackIdentifier": self_driving_task.get_cloudformation_key_prefix(environment),
        "ECRRepositoryArn": ecr_arn,
        **get_rds_credentials(project_name, environment, secrets_key, self_driving_task),
        **get_admin_credentials(project_name, environment, secrets_key, self_driving_task)
    }
    
    aws_secrets_client = boto3.client("secretsmanager", region_name=environment.get_aws_region())
    try:
        response = aws_secrets_client.get_secret_value(
            SecretId=secrets_key
        )
        known_params = {
            **known_params,
            **json.loads(response["SecretString"]),
        }
        secret_params = json.loads(response["SecretString"])
    except aws_secrets_client.exceptions.ResourceNotFoundException as rnfe:
        # logging.info(f"no secrets found for {secrets_key}")
        ...
    
    required_parameters, parameters_metadata = aws_utils.extract_cloudformation_params(cfn_file)
    missing = set()
    for param in required_parameters:
        param_meta = parameters_metadata.get(param, {})
        desc = str(param_meta.get("Description", "")).lower()
        has_default = "Default" in param_meta
        is_optional = "(optional)" in desc or has_default
        if param not in known_params and not is_optional:
            missing.add(param)
    
    if missing:
        raise AgentBlocked({
            "desc": "Missing required CloudFormation parameters",
            "missing_parameters": sorted(missing),
            "file": cfn_file.name,
            "secret_hint": f"/erieiron/{project_name}/{environment.value}/cloudformation"
        })
    
    return [
        {
            "ParameterKey": k,
            "ParameterValue": str(known_params[k])
        }
        for k in required_parameters
    ]


def get_admin_credentials(project_name, environment, secrets_key, self_driving_task):
    aws_secrets_client = boto3.client("secretsmanager", region_name=environment.get_aws_region())
    admin_secrets_key = f"{secrets_key}/appadmin"
    
    try:
        response = aws_secrets_client.get_secret_value(SecretId=admin_secrets_key)
        creds = json.loads(response)
    except Exception:
        creds = set_secret(aws_secrets_client, admin_secrets_key, {
            "AdminPassword": common.random_string(20)
        })
    
    return creds


def set_secret(aws_secrets_client, admin_secrets_key, json_val):
    try:
        aws_secrets_client.create_secret(
            Name=admin_secrets_key,
            SecretString=json.dumps(json_val)
        )
    except aws_secrets_client.exceptions.ResourceExistsException:
        aws_secrets_client.put_secret_value(
            SecretId=admin_secrets_key,
            SecretString=json.dumps(json_val)
        )
    
    return json_val


def get_rds_credentials(project_name, environment, secrets_key, self_driving_task):
    aws_secrets_client = boto3.client("secretsmanager", region_name=environment.get_aws_region())
    rds_secrets_key = f"{secrets_key}/rds"
    
    try:
        rds_secret = aws_secrets_client.get_secret_value(SecretId=rds_secrets_key)
        db_creds = json.loads(rds_secret["SecretString"])
    except aws_secrets_client.exceptions.ResourceNotFoundException:
        if AwsEnv.DEV.eq(environment):
            db_name = aws_utils.sanitize_aws_name([
                project_name,
                environment,
                self_driving_task.task.id
            ], max_length=50)
        else:
            db_name = aws_utils.sanitize_aws_name([
                project_name,
                environment
            ], max_length=50)
        
        db_creds = set_secret(aws_secrets_client, rds_secrets_key, {
            "DBPassword": common.random_string(20),
            "DBName": db_name
        })
    
    return db_creds


def get_file_structure_msg(root_dir: Path) -> LlmMessage:
    structure = {}
    skip_dirs = {".git", "vendor", "img", "compiled", "artifacts", ".idea", "env", "venv", "node_modules", "__pycache__"}
    
    for path in sorted(root_dir.glob("**/*")):
        # Skip any path that contains a directory in skip_dirs
        if any(part in skip_dirs for part in path.parts):
            continue
        
        relative_path = path.relative_to(root_dir)
        parts = relative_path.parts
        
        current = structure
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        if path.is_file():
            current[parts[-1]] = None
        else:
            current.setdefault(parts[-1], {})
    
    return LlmMessage.user(f"""
## Project File Structure (read-only reference for reuse planning)
{json.dumps(structure, indent=4)}
    """)
