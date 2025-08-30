import json
import logging
import os
import re
import subprocess
import time
import traceback
from collections import defaultdict
from pathlib import Path
from typing import List, Optional

import boto3
import botocore.session
import yaml
from django.db import transaction
from django.db.models import Func
from django.db.models import Q
from django.db.models.expressions import RawSQL
from erieiron_public import agent_tools
from sentence_transformers import SentenceTransformer

import settings
from erieiron_autonomous_agent.coding_agents import credential_manager
from erieiron_autonomous_agent.coding_agents.self_driving_coder_config import SelfDriverConfig, CodeReviewException, TASK_DESC_CODE_WRITING, BadPlan, GoalAchieved, RetryableException, AgentBlocked, PROMPTS_DIR, MAP_TASKTYPE_TO_PLANNING_PROMPT, SdaPhase, NeedPlan, ExecutionException, CloudFormationException
from erieiron_autonomous_agent.enums import TaskStatus
from erieiron_autonomous_agent.models import CodeVersion, CodeMethod, SelfDrivingTaskIteration, LlmRequest, Task, RunningProcess, SelfDrivingTask, CodeFile, AgentLesson, AgentTombstone, Business, Initiative
from erieiron_autonomous_agent.utils import codegen_utils
from erieiron_autonomous_agent.utils.codegen_utils import CodeCompilationError, get_codebert_embedding, validate_dockerfile
from erieiron_common import common, aws_utils
from erieiron_common.aws_utils import sanitize_aws_name
from erieiron_common.enums import LlmModel, PubSubMessageType, TaskType, TaskExecutionSchedule, AwsEnv, DevelopmentRoutingPath, LlmReasoningEffort, LlmVerbosity
from erieiron_common.llm_apis import llm_interface
from erieiron_common.llm_apis.llm_constants import MODEL_BACKUPS
from erieiron_common.llm_apis.llm_interface import LlmMessage, MODEL_TO_MAX_TOKENS, LlmResponse
from erieiron_common.message_queue.pubsub_manager import PubSubManager, pubsub_workflow

sentence_transformer_model = SentenceTransformer("all-MiniLM-L6-v2")

READONLY_FILES = [
    {
        "path": "manage.py",
        "alternatives": "settings.py",
        "description": "Django's core management script"
    }
]


@pubsub_workflow
def initialize_workflow(pubsub_manager: PubSubManager):
    pubsub_manager.on(
        PubSubMessageType.RESET_TASK_TEST,
        on_reset_task_test
    )


def execute(task_id: str, quick_debug=False, reset=False):
    self_driving_task = bootstrap_selfdriving_agent(task_id, reset)
    
    Task.objects.filter(id=self_driving_task.task_id).update(
        status=TaskStatus.IN_PROGRESS
    )
    
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
                
                if not config.business.codefile_set.exists():
                    config.set_phase(SdaPhase.INIT)
                    config.set_iteration(self_driving_task.iterate())
                    config.business.snapshot_code(
                        config.current_iteration,
                        include_erie_common=True
                    )
                
                if not config.business.architecture:
                    config.set_phase(SdaPhase.INIT)
                    write_business_architecture(config)
                
                if not config.initiative.architecture:
                    config.set_phase(SdaPhase.INIT)
                    write_initiative_architecture(config)
                
                if not config.business.required_credentials:
                    config.set_phase(SdaPhase.INIT)
                    config.set_iteration(self_driving_task.iterate())
                    identify_required_credentials(config)
                
                if not (self_driving_task.test_file_path and (config.sandbox_root_dir / self_driving_task.test_file_path).exists()):
                    config.set_phase(SdaPhase.CODING)
                    config.set_iteration(self_driving_task.iterate())
                    write_initial_test(config)
                
                elif not quick_debug and i == 0:
                    # we've re-started an self driving task - just execute on the first time around
                    most_recent_iteration = self_driving_task.get_most_recent_iteration()
                    if most_recent_iteration:
                        config.set_iteration(most_recent_iteration)
                    else:
                        config.set_iteration(self_driving_task.iterate())
                    
                    SelfDrivingTaskIteration.objects.filter(id=config.current_iteration.id).update(
                        log_content_execution="",
                        evaluation_json=None
                    )
                    config.current_iteration.refresh_from_db(fields=["log_content_execution", "evaluation_json"])
                else:
                    try:
                        most_recent_iteration = self_driving_task.get_most_recent_iteration()
                        if most_recent_iteration:
                            if quick_debug or not most_recent_iteration.planning_json:
                                config.set_iteration(most_recent_iteration)
                            else:
                                config.set_iteration(self_driving_task.iterate())
                        else:
                            config.set_iteration(self_driving_task.iterate())
                        
                        if not quick_debug and config.iteration_to_modify:
                            config.iteration_to_modify.write_to_disk()
                        
                        planning_data = plan_code_changes(config)
                        
                        do_coding(config, planning_data)
                    finally:
                        config.reset_log()
                
                execution_exception = None
                try:
                    execute_iteration(
                        config,
                        AwsEnv.DEV
                    )
                except BadPlan as bpe:
                    config.log(bpe)
                    execution_exception = bpe
                    raise bpe
                except AgentBlocked as abe:
                    execution_exception = abe
                    raise abe
                except NeedPlan as npe:
                    execution_exception = npe
                    raise npe
                except Exception as e:
                    execution_exception = e
                    config.log(e)
                finally:
                    config.set_phase(SdaPhase.EVALUATE)
                    evaluate_iteration_execution(config, execution_exception)
            except NeedPlan as npe:
                logging.info(f'NeedPlan - {npe}')
            except RetryableException as retryable_execution_exception:
                config.log(retryable_execution_exception)
                with transaction.atomic():
                    SelfDrivingTaskIteration.objects.filter(id=config.current_iteration.id).update(
                        log_content_execution=f"""
Execution Failed with 
{retryable_execution_exception}
{traceback.format_exc()}
            
planning data:
We should just try again - should be fixed next time around

full logs:
{config.get_log_content()}
                        """
                    )
                config.current_iteration.refresh_from_db(fields=["log_content_execution"])
            except BadPlan as bad_plan_exception:
                config.log(bad_plan_exception)
                with transaction.atomic():
                    SelfDrivingTaskIteration.objects.filter(id=config.current_iteration.id).update(
                        log_content_execution=f"""
Planning agent produced a bad plan:
{bad_plan_exception}
{traceback.format_exc()}

planning data:
{json.dumps(bad_plan_exception.plan_data, indent=4)}

full logs:
{config.get_log_content()}
                        """
                    )
                config.current_iteration.refresh_from_db(fields=["log_content_execution"])
            except AgentBlocked as agent_blocked:
                stop_reason = "Agent Blocked"
                config.log(agent_blocked)
                handle_agent_blocked(config, agent_blocked)
                break
            except GoalAchieved as goal_achieved:
                stop_reason = "Goal Achieved"
                handle_goal_achieved(config)
                break
            except Exception as e:
                logging.exception(e)
                config.log(e)
                config.supress_eval = True
                if config.self_driving_task.task_id:
                    PubSubManager.publish(
                        PubSubMessageType.TASK_FAILED,
                        payload={
                            "task_id": config.self_driving_task.task_id,
                            "error": traceback.format_exc()
                        }
                    )
                
                break
            finally:
                quick_debug = False
                config.cleanup_iteration()
    
    finally:
        config.log("DIR", config.git.source_root)
        
        config.log("STOP REASON", stop_reason)
        if TaskType.CODING_ML.eq(config.task_type):
            from erieiron_autonomous_agent.coding_agents.ml_packager import package_ml_artifacts
            package_ml_artifacts(config)


def handle_agent_blocked(config, agent_blocked):
    if not config.self_driving_task.task_id:
        return
        
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


def handle_goal_achieved(config):
    try:
        config.git.add_commit_push(
            f"task {config.task.id}: {config.task.description}"
        )
        
        config.git.cleanup()
        
        delete_cloudformation_stack(
            config,
            AwsEnv.DEV,
            block_while_waiting=False
        )
        
        if config.self_driving_task.task_id:
            PubSubManager.publish_id(
                PubSubMessageType.TASK_COMPLETED,
                config.self_driving_task.task_id
            )
        
        Task.objects.filter(id=config.task.id).update(
            status=TaskStatus.COMPLETE
        )
    except Exception as e:
        logging.exception(e)
        SelfDrivingTaskIteration.objects.filter(id=config.current_iteration.id).update(
            log_content_evaluation=traceback.format_exc()
        )
        Task.objects.filter(id=config.task.id).update(
            status=TaskStatus.FAILED
        )


def do_coding(config, planning_data):
    config.set_phase(SdaPhase.CODING)
    
    cr_exception = None
    failed_code_reviews = []
    for review_iteration_idx in range(5):
        try:
            implement_code_changes(
                config,
                planning_data,
                cr_exception
            )
            
            if not config.previous_iteration.has_error():
                perform_code_review(
                    config,
                    planning_data
                )
            
            break
        except CodeReviewException as code_review_exception:
            extract_lessons(
                config,
                TASK_DESC_CODE_WRITING,
                code_review_exception.review_data
            )
            
            failed_code_reviews.append(code_review_exception.review_data)
            cr_exception = code_review_exception
            if code_review_exception.bad_plan:
                raise BadPlan(
                    f"Code Review failed five times, time for a new plan.  this is all of code review blockers.",
                    {
                        "failed_code_reviews": failed_code_reviews
                    }
                )
            elif review_iteration_idx == 4:
                # out of retries
                raise BadPlan(
                    f"Code Review failed 5 times.  Need a new plan.  ",
                    code_review_exception.review_data
                )


def on_reset_task_test(task_id):
    task = Task.objects.get(id=task_id)
    self_driving_task = task.selfdrivingtask
    config = SelfDriverConfig(self_driving_task)
    write_initial_test(config)


def plan_code_changes(config):
    config.set_phase(SdaPhase.PLANNING)
    
    planning_data = None
    route_to = route_code_changes(config)
    
    if DevelopmentRoutingPath.ESCALATE_TO_PLANNER.eq(route_to):
        config.log(f"PHASE - plan_code_changes: {config.current_iteration.id}")
        planning_data = plan_full_code_changes(config)
    elif DevelopmentRoutingPath.AWS_PROVISIONING_PLANNER.eq(route_to):
        planning_data = plan_aws_provisioning_code_changes(config)
    elif DevelopmentRoutingPath.DIRECT_FIX.eq(route_to):
        planning_data = plan_direct_fix_code_changes(config)
    elif DevelopmentRoutingPath.ESCALATE_TO_HUMAN.eq(route_to):
        raise AgentBlocked(f"task {config.task.id} is blocked by {json.dumps(config.current_iteration.routing_json, indent=4)}")
    
    validate_plan(config, planning_data)
    
    SelfDrivingTaskIteration.objects.filter(id=config.current_iteration.id).update(
        planning_json=planning_data
    )
    config.current_iteration.refresh_from_db(fields=["planning_json"])
    
    for tombstone_data in common.get_list(planning_data, ["deprecation_plan", "tombstones"]):
        AgentTombstone.objects.update_or_create(
            business=config.business,
            name=tombstone_data.get("name"),
            defaults={
                "data_json": tombstone_data
            }
        )
    
    return planning_data


def validate_plan(config: SelfDriverConfig, planning_data):
    readonly_paths = get_readonly_files_paths(config)
    
    for f in planning_data.get("code_files", []):
        code_file_path = str(f.get("code_file_path"))
        
        if code_file_path.endswith("settings.py"):
            continue
        
        if "docker-compose" in code_file_path:
            raise BadPlan(f"All services must be defined in the existing Dockerfile. You may **never** use docker compose", planning_data)
        
        if code_file_path.startswith("erieiron_common"):
            raise BadPlan(f"You may not add or edit any file in erieiron_common.  These are readonly library files.", planning_data)
        
        if code_file_path in readonly_paths:
            readonly_data = readonly_paths[code_file_path]
            raise BadPlan(f"You may not edit {code_file_path}. {readonly_data['description']}  If you think you need to modify {code_file_path}, you might need to modify {readonly_data['alternatives']} instead", planning_data)
        
        if code_file_path.endswith("manage.py"):
            raise BadPlan(f"You may not edit manage.py.  If you need to modify django settings, modify settings.py instead", planning_data)
        
        if code_file_path.endswith(".py") and "/" not in code_file_path[2:]:
            raise BadPlan(f"python files cannot live in the root directory.  bad:  {code_file_path}", planning_data)
    
    return planning_data


def bootstrap_selfdriving_agent(task_id, reset: False) -> SelfDrivingTask:
    task = Task.objects.get(id=task_id)
    self_driving_task: SelfDrivingTask = task.create_self_driving_env(
        reset_code_dir=reset
    )
    
    git = self_driving_task.get_git()
    git.pull()
    
    if reset:
        self_driving_task.selfdrivingtaskiteration_set.all().delete()
    
    if not self_driving_task.selfdrivingtaskiteration_set.exists():
        config = SelfDriverConfig(self_driving_task)
        config.set_iteration(self_driving_task.iterate())
        
        config.business.snapshot_code(
            config.current_iteration,
            include_erie_common=False
        )
        
        has_cloudformation = config.business.codefile_set.filter(
            file_path="infrastructure.yaml"
        ).exists()
        
        has_completed_tasks = config.business.selfdrivingtask_set.filter(
            test_file_path__isnull=False,
            task__status=TaskStatus.COMPLETE
        ).exists()
        
        if has_cloudformation and has_completed_tasks:
            # make sure the tests run
            delete_cloudformation_stack(config, AwsEnv.DEV)
            execute_iteration(
                config,
                AwsEnv.DEV
            )
            
            assert_tests_green(config)
    
    return self_driving_task


def build_docker_image(
        config: SelfDriverConfig,
        envinronment: AwsEnv,
        docker_env: dict,
        docker_file: Path
) -> str:
    exec_docker_prune()
    
    current_iteration = config.current_iteration
    self_driving_task = current_iteration.self_driving_task
    
    docker_image_tag = sanitize_aws_name([
        self_driving_task.business.name,
        self_driving_task.id,
        current_iteration.version_number
    ], max_length=128)
    
    config.log(f"\n\n\n\n======== Begining DOCKER Build for tag {docker_image_tag} ")
    sandbox_path = self_driving_task.sandbox_path
    
    requirements_txt = Path(sandbox_path) / "requirements.txt"
    
    docker_build_cmd = common.strings([
        "docker",
        "build",
        "-t", docker_image_tag,
        "-f", docker_file,
        docker_file.parent
    ])
    
    config.log(f"\n\nstarting docker build with the command:\n{' '.join(docker_build_cmd)}\n\n")
    build_process = subprocess.Popen(
        docker_build_cmd,
        stdout=config.log_f,
        stderr=subprocess.STDOUT,
        text=True,
        env=docker_env
    )
    
    while build_process.poll() is None:
        time.sleep(1)
    
    if build_process.returncode != 0:
        raise Exception(f"Docker build failed with return code: {build_process.returncode}")
    
    config.log(f"""
=========================================================
if you want to debug the docker container, run this 

docker run --rm -it -v {self_driving_task.sandbox_path}:/app -w /app {docker_image_tag} /bin/bash

=========================================================
        """)
    
    return docker_image_tag


def exec_docker_prune():
    try:
        subprocess.run(["docker", "system", "prune", "-f"], check=True)
    except Exception as e:
        logging.exception(e)
        raise AgentBlocked("unable to run docker prune - is docker running?")


def get_role_from_cloudformation_stack(config: SelfDriverConfig, aws_env: AwsEnv, os_env: dict):
    stack_name = config.self_driving_task.get_cloudformation_stack_name(aws_env)
    if not stack_name:
        raise AgentBlocked("unable to determine stack name")
    
    region = get_aws_region()
    if not region:
        raise AgentBlocked("unable to determine regions")
    
    stack_descriptions = boto3.client(
        "cloudformation",
        region_name=region
    ).describe_stacks(
        StackName=stack_name
    ).get("Stacks")
    if not stack_descriptions:
        raise AgentBlocked(f"no stack descriptions found for {stack_name}")
    
    outputs = common.ensure_list(common.first(stack_descriptions).get("Outputs"))
    if not outputs:
        raise AgentBlocked(f"no stack outputs found for {stack_name}")
    
    role_arn = None
    # Prefer explicit key, else fall back to anything that looks like a task role arn
    for o in outputs:
        if o.get("OutputKey") == "DjangoEcsTaskRoleArn" and o.get("OutputValue"):
            role_arn = o["OutputValue"]
            break
    
    if not role_arn:
        for o in outputs:
            val = o.get("OutputValue", "")
            if ":role/" in val:
                role_arn = val
                break
    if role_arn:
        logging.info(f"Resolved role to assume from CloudFormation outputs: {role_arn}")
    
    if not role_arn:
        # Nothing to do; caller may rely on container-provided identity (e.g., ECS task role)
        raise BadPlan("infrastructure.yaml does not define a TaskRoleArn")
    
    return role_arn


def get_aws_region():
    return os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or settings.AWS_DEFAULT_REGION_NAME


def get_env_var_names(config: SelfDriverConfig) -> str:
    env = build_env(config, AwsEnv.DEV)
    return ", ".join(env.keys())


def build_env(config: SelfDriverConfig, aws_env: AwsEnv) -> dict:
    env = {}
    
    required_credentials = config.business.required_credentials
    
    for credential_service_name, cred_def in required_credentials.items():
        secret_arn_env_var = cred_def.get("secret_arn_env_var")
        secrent_arn = credential_manager.manage_credentials(
            config,
            aws_env,
            credential_service_name,
            cred_def
        )
        env[secret_arn_env_var] = secrent_arn
    
    aws_credentials = botocore.session.Session(
        profile=os.environ.get("AWS_PROFILE")
    ).get_credentials().get_frozen_credentials()
    
    env["AWS_DEFAULT_REGION"] = settings.AWS_DEFAULT_REGION_NAME
    env["AWS_ACCOUNT_ID"] = settings.AWS_ACCOUNT_ID
    env["AWS_ACCESS_KEY_ID"] = aws_credentials.access_key
    env["AWS_SECRET_ACCESS_KEY"] = aws_credentials.secret_key
    env["AWS_SESSION_TOKEN"] = aws_credentials.token
    env["LLM_API_KEYS_SECRET_ARN"] = settings.LLM_API_KEYS_SECRET_ARN
    env["TASK_NAMESPACE"] = env["CLOUDFORMATION_STACK_NAME"] = config.self_driving_task.get_cloudformation_stack_name(aws_env)
    env["DOCKER_BUILDKIT"] = "1"
    env["PATH"] = os.getenv("PATH")
    
    for k in list(env.keys()):
        if env.get(k) is None:
            env.pop(k, None)
    
    return env


def build_env_flags(env):
    env_flags: list[str] = []
    for k in list(env.keys()):
        if k in ["PATH"]:
            continue
        
        val = env.get(k)
        if val:
            env_flags += ["-e", f"{k}={val}"]
    
    return env_flags


def push_image_to_ecr(
        config: SelfDriverConfig,
        envinronment: AwsEnv,
        docker_image_tag: str
):
    region = envinronment.get_aws_region()
    ecr_client = boto3.client("ecr", region_name=region)
    account_id = boto3.client("sts").get_caller_identity()["Account"]
    
    repo_name = sanitize_aws_name(config.business.service_token)
    ecr_repo_uri = f"{account_id}.dkr.ecr.{region}.amazonaws.com/{repo_name}"
    
    full_image_uri = f"{ecr_repo_uri}:{docker_image_tag}"
    config.log(f"\n\n\n\n======== Begining ECR Push to {full_image_uri} ")
    
    try:
        ecr_client.describe_repositories(repositoryNames=[repo_name])
    except ecr_client.exceptions.RepositoryNotFoundException:
        ecr_client.create_repository(repositoryName=repo_name)
    
    repo_desc = ecr_client.describe_repositories(repositoryNames=[repo_name])
    ecr_arn = repo_desc["repositories"][0]["repositoryArn"]
    
    subprocess.run(
        ["docker", "tag", docker_image_tag, full_image_uri],
        check=True,
        stdout=config.log_f,
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
        stdout=config.log_f,
        stderr=subprocess.STDOUT,
        env=env
    )
    config.log(f"======== COMPLETED ECR Push to {full_image_uri}\n\n\n\n")
    
    return full_image_uri, ecr_arn


def run_docker_command(
        config: SelfDriverConfig,
        aws_env: AwsEnv,
        command_args: list[str],
        docker_env: dict,
        running_process: RunningProcess,
        docker_image: str
) -> None:
    command_args = common.ensure_list(command_args)
    task_execution = running_process.task_execution
    iteration = config.current_iteration
    selfdriving_task = iteration.self_driving_task
    sandbox_path = iteration.self_driving_task.sandbox_path
    
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{sandbox_path}:/app",
        "-w", "/app",
        *build_env_flags(docker_env),
        docker_image,
        "python", "manage.py",
        *common.safe_strs(command_args)
    ]
    
    config.log("\n" + "=" * 50 + "\n")
    config.log(f"RUNNING {' '.join(cmd)} in {sandbox_path}\n")
    config.log("=" * 50 + "\n")
    
    # Capture docker run start time
    start_epoch = int(time.time())
    process = subprocess.Popen(
        cmd,
        stdout=config.log_f,
        env=docker_env,
        stderr=subprocess.STDOUT,
        text=True
    )
    
    # Update running process with PID
    running_process.process_id = process.pid
    running_process.save(update_fields=['process_id'])
    
    config.log(f"Docker {command_args[-1]} execution started with PID {process.pid}, iteration_id: {iteration.id}")
    
    # Wait for completion
    while process.poll() is None:
        running_process.update_log_tail()
        time.sleep(2)
    
    end_epoch = int(time.time())
    return_code = process.returncode
    config.log(f"\n{command_args[-1]} execution completed with return code: {return_code}\n")
    
    running_process.update_log_tail()
    
    if return_code != 0:
        enriched_logs = enrich_local_logs_with_cloudwatch_logs(
            config.get_log_content(),
            config,
            aws_env,
            start_epoch,
            end_epoch
        )
        
        task_execution.error_msg = enriched_logs
        task_execution.save()
        
        extracted_exception = extract_exception(config, enriched_logs)
        raise ExecutionException(extracted_exception)


def enrich_local_logs_with_cloudwatch_logs(
        local_logs,
        config,
        aws_env,
        start_epoch,
        end_epoch
):
    # Attempt to enrich error with relevant CloudWatch Lambda logs (by RequestId found in local logs)
    try:
        cw_text = extract_cloudwatch_lambda_logs(
            local_logs=local_logs,
            config=config,
            lookback_minutes=120
        )
        
        if cw_text:
            config.log("\n\n[CloudWatch enrichment]\n" + cw_text + "\n")
            local_logs = local_logs + "\n\n==== CloudWatch Logs (enriched) ====\n" + cw_text
        else:
            config.log("No AWS Lambda RequestId found in local logs; skipping CloudWatch enrichment.")
    except Exception as cw_e:
        config.log(f"CloudWatch enrichment failed: {cw_e}")
        local_logs = config.get_log_content() or ""
    
    # Also collect stack-scoped CloudWatch logs within the docker execution window
    try:
        stack_scoped = extract_cloudwatch_stack_logs_for_window(
            config=config,
            start_time=start_epoch - 5,  # small buffer
            end_time=end_epoch + 5
        )
        if stack_scoped:
            config.log("\n\n[CloudWatch stack-window enrichment]\n" + stack_scoped + "\n")
            local_logs = local_logs + "\n\n==== CloudWatch Logs (stack scoped, docker window) ====\n" + stack_scoped
    except Exception as sw_e:
        config.log(f"CloudWatch stack-window enrichment failed: {sw_e}")
    
    return local_logs


def extract_exception(config: SelfDriverConfig, log_content: str) -> str:
    return llm_chat(
        "Log Extraction",
        config,
        [
            get_sys_prompt("log_parser.md"),
            log_content
        ],
        model=LlmModel.OPENAI_GPT_5,
        reasoning_effort=LlmReasoningEffort.HIGH,
        verbosity=LlmVerbosity.LOW
    ).text


# Helper to extract CloudWatch Lambda logs given RequestIds
def extract_cloudwatch_lambda_logs(
        local_logs: str,
        config: SelfDriverConfig,
        lookback_minutes: int = 60,
        max_groups: int = 50
) -> str:
    """
    Fetch CloudWatch Logs Insights for Lambda log groups that contain any of the given RequestIds.
    We scan recent logs (lookback window) across up to `max_groups` Lambda log groups.
    Returns a concatenated text block, sorted by timestamp, suitable for appending to error output.
    """
    request_ids = re.findall(r"RequestId:\s*([0-9a-fA-F\-]{36})", local_logs)
    if not request_ids:
        return ""
    
    logs = boto3.client("logs", region_name=get_aws_region())
    
    # Discover Lambda log groups (limit to keep queries bounded)
    log_groups = []
    next_token = None
    try:
        while True:
            kwargs = {"logGroupNamePrefix": "/aws/lambda/"}
            if next_token:
                kwargs["nextToken"] = next_token
            resp = logs.describe_log_groups(**kwargs)
            for lg in resp.get("logGroups", []):
                name = lg.get("logGroupName")
                if name:
                    log_groups.append(name)
                    if len(log_groups) >= max_groups:
                        break
            if len(log_groups) >= max_groups or not resp.get("nextToken"):
                break
            next_token = resp.get("nextToken")
    except Exception as e:
        config.log(f"Failed to list CloudWatch log groups: {e}")
        return ""
    
    if not log_groups:
        return ""
    
    end = int(time.time())
    start = end - lookback_minutes * 60
    
    # Build a Logs Insights query that matches any of the request IDs
    # We OR the patterns to reduce the number of queries.
    request_ids = [rid for rid in request_ids if isinstance(rid, str) and len(rid) >= 36]
    if not request_ids:
        return ""
    
    # Escape single quotes in IDs just in case
    or_terms = ["@message like '" + rid.replace("'", "\\'") + "'" for rid in request_ids]
    or_filters = " or ".join(or_terms)
    query_str = "fields @timestamp, @log, @message | filter " + or_filters + " | sort @timestamp asc | limit 1000"
    
    combined = []
    # Run the query against all discovered groups in small batches to stay within API limits
    batch_size = 10
    for i in range(0, len(log_groups), batch_size):
        batch = log_groups[i:i + batch_size]
        try:
            q = logs.start_query(
                logGroupNames=batch,
                startTime=start,
                endTime=end,
                queryString=query_str
            )
            query_id = q["queryId"]
        except Exception as e:
            config.log(f"start_query failed for batch {batch}: {e}")
            continue
        
        # Poll for completion
        status = "Running"
        for _ in range(60):
            time.sleep(1)
            resp = logs.get_query_results(queryId=query_id)
            status = resp.get("status")
            if status in ("Complete", "Failed", "Cancelled", "Timeout"):
                results = resp.get("results", [])
                # Flatten results into text lines
                for item in results:
                    fields = {f.get("field"): f.get("value") for f in item}
                    ts = fields.get("@timestamp", "")
                    lg = fields.get("@log", "")
                    msg = fields.get("@message", "")
                    combined.append(f"{ts}  {lg}\n{msg}")
                break
        
        if status != "Complete":
            config.log(f"Logs Insights query did not complete (status={status}) for batch {batch}")
    
    return "\n\n".join(combined)


def execute_iteration(config: SelfDriverConfig, aws_env: AwsEnv) -> str:
    running_process = None
    
    iteration = config.current_iteration
    self_driving_task = iteration.self_driving_task
    
    task = self_driving_task.task
    task_type = TaskType(task.task_type)
    task_execution = init_task_execution(iteration)
    
    try:
        config.set_phase(SdaPhase.DEPLOY)
        
        docker_env = build_env(
            config,
            aws_env
        )
        
        running_process, _ = RunningProcess.objects.update_or_create(
            task_execution=task_execution,
            execution_type='docker',
            log_file_path=str(config.log_path)
        )
        
        docker_file = config.sandbox_root_dir / "Dockerfile"
        ecr_authenticate_for_dockerfile(
            config,
            docker_file
        )
        
        docker_image_tag = build_docker_image(
            config,
            aws_env,
            docker_env,
            docker_file
        )
        
        SelfDrivingTaskIteration.objects.filter(id=iteration.id).update(
            docker_tag=docker_image_tag
        )
        iteration.refresh_from_db(fields=["docker_tag"])
        
        infrastructure_code_version = iteration.codeversion_set.filter(
            code_file=CodeFile.get(config.business, "infrastructure.yaml")
        ).first()
        
        if not stack_exists(config, aws_env, docker_env) or (
                infrastructure_code_version
                and infrastructure_code_version.get_diff()
        ):
            try:
                full_image_uri, ecr_arn = push_image_to_ecr(
                    config,
                    aws_env,
                    docker_image_tag
                )
            except Exception as e:
                raise AgentBlocked(f"task {task.id} is failing to push {docker_image_tag} to ECR. {e}")
            
            if AwsEnv.DEV.eq(aws_env):
                try:
                    empty_stack_buckets(config)
                except Exception as e:
                    logging.exception(e)
                    raise AgentBlocked(f"unable to empty buckets for stack {config.self_driving_task.cloudformation_stack_name}")
            
            deploy_cloudformation_stacks(
                config,
                aws_env,
                docker_env,
                ecr_arn
            )
        
        manage_db(
            config,
            aws_env,
            docker_env,
            docker_image_tag,
            running_process
        )
        
        config.set_phase(SdaPhase.EXECUTION)
        if TaskType.CODING_ML.eq(task_type):
            run_docker_command(
                config=config,
                aws_env=aws_env,
                docker_env=docker_env,
                command_args=self_driving_task.main_name,
                running_process=running_process,
                docker_image=docker_image_tag
            )
            config.log_f.flush()  # Ensure ML execution logs are visible to tailing thread
        else:
            run_docker_command(
                config=config,
                aws_env=aws_env,
                docker_env=docker_env,
                command_args="test",
                running_process=running_process,
                docker_image=docker_image_tag
            )
            
            if TaskType.TASK_EXECUTION.eq(task_type) and TaskExecutionSchedule.ONCE.eq(task.execution_schedule):
                task_io_dir = Path(self_driving_task.sandbox_path) / "task_io"
                task_io_dir.mkdir(parents=True, exist_ok=True)
                
                input_file = task_io_dir / f"{task.id}-input.json"
                common.write_json(input_file, task.get_upstream_outputs())
                
                output_file = task_io_dir / f"{task.id}-output.json"
                
                run_docker_command(
                    config=config,
                    aws_env=aws_env,
                    docker_env=docker_env,
                    command_args=[
                        self_driving_task.main_name,
                        "--input_file", input_file,
                        "--output_file", output_file
                    ],
                    running_process=running_process,
                    docker_image=docker_image_tag
                )
        
        config.log("Docker execution finished")
    finally:
        if running_process and running_process.is_running:
            running_process.update_log_tail()
            running_process.is_running = False
            running_process.terminated_at = common.get_now()
            running_process.save(update_fields=['is_running', 'terminated_at'])
        
        config.business.snapshot_code(iteration, include_erie_common=False)
        exec_docker_prune()


def stack_exists(config: SelfDriverConfig, aws_env: AwsEnv, docker_env: dict) -> bool:
    try:
        matching_stack = get_stack(
            config.self_driving_task.get_cloudformation_stack_name(aws_env),
            boto3.client("cloudformation", region_name=docker_env['AWS_DEFAULT_REGION'])
        )
        
        return matching_stack is not None
    except:
        return False


def manage_db(
        config: SelfDriverConfig,
        aws_env: AwsEnv,
        docker_env: dict,
        docker_image_tag: str,
        running_process: RunningProcess
):
    aws_region_name = docker_env["AWS_DEFAULT_REGION"]
    
    rds_secret = agent_tools.get_secret_json(
        docker_env["RDS_SECRET_ARN"],
        aws_region_name
    )
    
    # aws_utils.ensure_network_access(
    #     rds_secret["host"],
    #     aws_region_name
    # )
    
    run_docker_command(
        config=config,
        aws_env=aws_env,
        docker_env=docker_env,
        command_args="migrate",
        running_process=running_process,
        docker_image=docker_image_tag
    )


def init_task_execution(iteration):
    task = iteration.self_driving_task.task
    
    task_input = {}
    for upstream_task in task.depends_on.all():
        if not TaskStatus.COMPLETE.eq(upstream_task.status):
            raise AgentBlocked(f"task {task.id} depends on task {upstream_task.id}, but the upstream task's status is {upstream_task.status}")
        
        if TaskType.TASK_EXECUTION.eq(iteration.self_driving_task.task.task_type):
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


def assert_tests_green(config: SelfDriverConfig):
    test_reviewer_output = llm_chat(
        "Assert Initial Tests Green",
        config,
        [
            get_sys_prompt("test_reviewer.md"),
            config.get_log_content()
        ],
        output_schema="test_reviewer.md.schema.json",
        model=LlmModel.OPENAI_GPT_5,
        reasoning_effort=LlmReasoningEffort.LOW,
        verbosity=LlmVerbosity.LOW
    ).json()
    
    if not test_reviewer_output.get("all_passed"):
        raise AgentBlocked({
            "desc": "assert_tests_green failed",
            **test_reviewer_output
        })


def evaluate_iteration_execution(config: SelfDriverConfig, exception: Exception):
    iteration: SelfDrivingTaskIteration = SelfDrivingTaskIteration.objects.get(id=config.current_iteration.id)
    
    if isinstance(exception, AgentBlocked):
        raise exception
    
    if isinstance(exception, NeedPlan):
        return
    
    log_output = config.log_path.read_text()
    
    if "no space left on device" in common.default_str(log_output).lower():
        subprocess.run(["docker", "system", "prune", "-a", "-f"], check=True)
        raise RetryableException(f"execution is failing with 'no space left on device'\n\n{log_output}.  I just pruned docker, so should be cleared up now.")
    
    messages = ([
        get_sys_prompt([
            "iteration_summarizer.md",
            "common--iam_role.md",
            "common--forbidden_actions.md",
            "common--environment_variables.md"
        ], replacements=[
            ("<env_vars>", get_env_var_names(config)),
        ])
    ])
    
    if isinstance(exception, ExecutionException):
        messages += LlmMessage.user_from_data(
            f"**Exception throw during this iteration's execution**",
            {
                "exception": str(exception)
            }
        )
    else:
        messages += LlmMessage.user_from_data(
            f"**Logs from the iteration's test output and execution**",
            {
                "log_output": log_output
            }
        )
        
        if exception:
            messages += LlmMessage.user_from_data(
                f"**Exception throw during this iteration's execution**",
                {
                    "exception": common.get_stack_trace_as_string(exception)
                }
            )
    
    messages.append(LlmMessage.user("Please summarize this iteration"))
    
    eval_data = llm_chat(
        "Iteration Summarizer",
        config,
        messages,
        LlmModel.OPENAI_GPT_5_NANO,
        output_schema="iteration_summarizer.md.schema.json"
    ).json()
    
    if isinstance(exception, ExecutionException):
        common.struct_set(eval_data, ['error', 'logs'], str(exception))
    
    with transaction.atomic():
        SelfDrivingTaskIteration.objects.filter(id=iteration.id).update(
            evaluation_json=eval_data
        )
    
    previous_iteration_evals = []
    if config.iteration_to_modify and config.iteration_to_modify != config.previous_iteration:
        previous_iterations = config.self_driving_task.selfdrivingtaskiteration_set.filter(
            evaluation_json__isnull=False,
            timestamp__gte=config.iteration_to_modify.timestamp
        ).order_by("timestamp")
    else:
        previous_iterations = config.self_driving_task.selfdrivingtaskiteration_set.filter(
            evaluation_json__isnull=False
        ).order_by("-timestamp")[:3][::-1]
    
    for prev_iter in previous_iterations:
        previous_iteration_evals.append({
            "iteration_id": prev_iter.id,
            "iteration_is_current_iteration": prev_iter == iteration,
            "iteration_timestamp": prev_iter.timestamp,
            "iteration_evaluation": prev_iter.evaluation_json,
        })
    
    selection_data = llm_chat(
        "Iteration Selector",
        config,
        [
            get_sys_prompt([
                "iteration_selector.md",
                "common--iam_role.md",
                "common--forbidden_actions.md",
                "common--environment_variables.md"
            ], replacements=[
                ("<env_vars>", get_env_var_names(config)),
            ]),
            *LlmMessage.user_from_data(
                f"**Iteration Evaluations**",
                previous_iteration_evals
            )
        ],
        LlmModel.OPENAI_GPT_5_MINI,
        output_schema="iteration_selector.md.schema.json"
    ).json()
    
    iteration_id_to_modify = selection_data.get("iteration_id_to_modify")
    if iteration_id_to_modify != 'latest':
        count_prevous_attempts = SelfDrivingTaskIteration.objects.filter(start_iteration_id=iteration_id_to_modify).count()
        if count_prevous_attempts < 5:
            selection_data['iteration_id_to_modify'] = iteration_id_to_modify
        else:
            # we've tried too many times.  go with the latest to see if this improves
            selection_data['iteration_id_to_modify'] = 'latest'
    
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
    
    extract_lessons(
        config,
        "code deploy and execution",
        log_output
    )
    
    return eval_data


def prepare_stack_for_update(config: SelfDriverConfig, stack_name: str, cf_client):
    for i in range(5):
        matching_stack = get_stack(stack_name, cf_client)
        if not matching_stack:
            return
        
        try:
            status = matching_stack["StackStatus"]
            if 'ROLLBACK' in status:
                config.log(f"Deleting broken stack: {stack_name} (status: {status}) attempt {i + 1}\n")
                
                try:
                    resources = cf_client.describe_stack_resources(StackName=stack_name)['StackResources']
                    for r in resources:
                        if r['ResourceStatus'] == 'DELETE_FAILED':
                            config.log(f"Resource {r['LogicalResourceId']} stuck in DELETE_FAILED. Manual cleanup may be required.\n")
                            # Optional: custom cleanup logic could go here
                except Exception as e:
                    config.log(f"Failed to describe stack resources: {e}\n")
                cf_client.delete_stack(StackName=stack_name)
            
            cloudformation_wait(config, cf_client, stack_name)
        except cf_client.exceptions.ClientError as e:
            if "does not exist" in str(e):
                ...
            else:
                config.log(traceback.format_exc())
                raise


def cloudformation_wait(
        config: SelfDriverConfig,
        cf_client,
        stack_name,
        timeout=45 * 60,
        poll_interval=10,
        throw_on_fail=False
):
    start_time = time.time()
    
    while True:
        time.sleep(poll_interval)
        
        stack = get_stack(stack_name, cf_client)
        if not stack:
            return
        
        status = stack['StackStatus']
        if throw_on_fail:
            if status.startswith("ROLLBACK_"):
                break
        
        if not status.endswith("_IN_PROGRESS"):
            break
        
        wait_time = time.time() - start_time
        if wait_time > timeout:
            raise TimeoutError(f"Timeout waiting for stack {stack_name} to reach a terminal state. Last status: {status}")
        
        config.log(f"waiting on {stack_name}.  status: {status}. waiting {int(wait_time)}s out of a max wait of {timeout}s")
    
    if throw_on_fail:
        assert_cloudformation_stack_valid(stack_name, cf_client)


def get_stack(stack_name, cf_client):
    try:
        return common.first(cf_client.describe_stacks(StackName=stack_name)['Stacks'])
    except:
        return None


def extract_cloudformation_params(cfn_file: Path):
    data = yaml.load(cfn_file.read_text(), Loader=yaml.BaseLoader)
    param_metadata = data.get("Parameters") or {}
    
    required_params = {
        name
        for name, meta in param_metadata.items()
        if not isinstance(meta, dict) or ("Default" not in meta and "(optional)" not in str(meta.get("Description", "")).lower())
    }
    
    return required_params, param_metadata


def push_cloudformation(
        config: SelfDriverConfig,
        stack_name: str,
        environment: AwsEnv,
        cfn_file: Path,
        param_list: list
):
    start_time = time.time()
    cf_client = boto3.client("cloudformation", region_name=environment.get_aws_region())
    try:
        config.log(f"pushing {stack_name} to {environment.get_aws_region()} with {cfn_file}")
        template_body = cfn_file.read_text()
        
        if get_stack(stack_name, cf_client):
            assert_cloudformation_stack_valid(stack_name, cf_client)
            
            config.log(f"Updating existing stack: {stack_name}\n")
            try:
                cf_client.update_stack(
                    StackName=stack_name,
                    TemplateBody=template_body,
                    Parameters=param_list,
                    Capabilities=["CAPABILITY_NAMED_IAM"]
                )
            except Exception as deploy_exception:
                if "No updates are to be performed" not in str(deploy_exception):
                    raise deploy_exception
        else:
            config.log(f"Creating new stack: {stack_name}\n")
            cf_client.create_stack(
                StackName=stack_name,
                TemplateBody=template_body,
                Parameters=param_list,
                Capabilities=["CAPABILITY_NAMED_IAM"]
            )
        
        cloudformation_wait(
            config,
            cf_client,
            stack_name,
            throw_on_fail=True
        )
        
        config.log(f"CloudFormation stack {stack_name} deployed successfully.\n")
    finally:
        compute_cfn_resource_durations(
            config,
            cf_client,
            stack_name,
            stack_name
        )
        
        push_time_mins = (time.time() - start_time) / 60
        if push_time_mins > 10:
            config.log(f"ERROR! cloudformation deploy for {stack_name} took {push_time_mins} minutes, which is too long!  CODE PLANNER:  think of ways to make cloudformation updates faster\n\n\n\n")


def compute_cfn_resource_durations(config: SelfDriverConfig, cf_client, stack_name, deployment_start_datetime):
    try:
        events = cf_client.describe_stack_events(StackName=stack_name).get('StackEvents', [])
    except Exception:
        return []
    
    from datetime import datetime, timezone as dt_timezone
    if isinstance(deployment_start_datetime, str):
        try:
            # Attempt ISO 8601 parsing
            deployment_start_datetime = datetime.fromisoformat(deployment_start_datetime)
            # If parsed value is naive, make it UTC
            if deployment_start_datetime.tzinfo is None:
                deployment_start_datetime = deployment_start_datetime.replace(tzinfo=dt_timezone.utc)
        except Exception:
            # If parsing fails, fall back to including all events
            deployment_start_datetime = datetime.min.replace(tzinfo=dt_timezone.utc)
    elif isinstance(deployment_start_datetime, datetime):
        # Ensure awareness; boto3 timestamps are tz-aware
        if deployment_start_datetime.tzinfo is None:
            deployment_start_datetime = deployment_start_datetime.replace(tzinfo=dt_timezone.utc)
    else:
        # Unknown type; include all events to avoid type errors
        deployment_start_datetime = datetime.min.replace(tzinfo=dt_timezone.utc)
    
    recent_events = [e for e in events if e.get('Timestamp') and e['Timestamp'] >= deployment_start_datetime]
    recent_events.sort(key=lambda x: x['Timestamp'])
    
    events_by_logical = defaultdict(list)
    for e in recent_events:
        # Some events can be for the stack itself (LogicalResourceId == stack name)
        logical_id = e.get('LogicalResourceId')
        if not logical_id:
            continue
        events_by_logical[logical_id].append(e)
    
    durations = []
    for logical_id, evs in events_by_logical.items():
        # Scan sequences: find segments from *_IN_PROGRESS to next terminal status
        longest_seconds = None
        best_pair = None
        i = 0
        n = len(evs)
        while i < n:
            status_i = evs[i].get('ResourceStatus', '')
            if status_i.endswith('IN_PROGRESS'):
                start_ev = evs[i]
                j = i + 1
                # Find the next terminal event for this resource
                while j < n:
                    status_j = evs[j].get('ResourceStatus', '')
                    if not status_j.endswith('IN_PROGRESS'):
                        end_ev = evs[j]
                        delta_sec = (end_ev['Timestamp'] - start_ev['Timestamp']).total_seconds()
                        if longest_seconds is None or delta_sec > longest_seconds:
                            longest_seconds = delta_sec
                            best_pair = (start_ev, end_ev)
                        break
                    j += 1
                i = j
                continue
            i += 1
        
        if longest_seconds is not None and best_pair:
            # Prefer resource_type from terminal event, fall back to start event
            res_type = best_pair[1].get('ResourceType') or best_pair[0].get('ResourceType')
            durations.append({
                'logical_id': logical_id,
                'resource_type': res_type,
                'seconds': round(float(longest_seconds), 3)
            })
    
    durations.sort(key=lambda d: d['seconds'], reverse=True)
    
    SelfDrivingTaskIteration.objects.filter(id=config.current_iteration.id).update(
        slowest_cloudformation_resources=durations
    )
    config.current_iteration.refresh_from_db(fields=["slowest_cloudformation_resources"])
    
    return durations


def assert_cloudformation_stack_valid(stack_name, cf_client):
    matching_stack = get_stack(stack_name, cf_client)
    if not matching_stack:
        raise CloudFormationException(f"CloudFormation stack {stack_name} doesn't exist")
    
    status = matching_stack['StackStatus']
    if "FAILED" in status or "ROLLBACK" in status:
        raise CloudFormationException(f"CloudFormation stack {stack_name} failed with status: {status}")


def build_cloudformation_durations_context_messages(config: SelfDriverConfig, title=None) -> List[LlmMessage]:
    iteration_to_modify = config.iteration_to_modify
    if not (iteration_to_modify and iteration_to_modify.slowest_cloudformation_resources):
        return []
    
    return LlmMessage.user_from_data(
        "Slowest CloudFormation Resources",
        {
            "cloudformation_durations": iteration_to_modify.slowest_cloudformation_resources
        }
    )


def build_previous_iteration_context_messages(config: SelfDriverConfig, title=None) -> List[LlmMessage]:
    current_iteration = config.current_iteration
    previous_iteration = config.previous_iteration
    iteration_to_modify = config.iteration_to_modify
    
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
        config,
        previous_iterations,
        title=title
    )
    
    if iteration_to_modify and iteration_to_modify != previous_iteration:
        # iteration_to_modify.id
        previous_attempts = SelfDrivingTaskIteration.objects.filter(
            start_iteration=iteration_to_modify
        ).order_by("timestamp")
        
        messages += get_iteration_eval_llm_messages(
            config,
            previous_attempts,
            title=f"You have made {previous_attempts.count()} attempt(s) to make progress on iteration {iteration_to_modify.id}.  Here are the results of these previous failed attempts at making progress.  Learn from historic failures and don't repeat these mistakes"
        )
    
    return messages


def get_iteration_eval_llm_messages(
        config: SelfDriverConfig,
        iterations: list[SelfDrivingTaskIteration],
        title=None
) -> list[LlmMessage]:
    title = title or "**Output from iteration_evaluator agent**"
    previous_iteration = config.previous_iteration
    iteration_to_modify = config.iteration_to_modify
    
    all_iteration_ids = [i.id for i in common.filter_none(common.ensure_list(iterations) + [
        previous_iteration,
        iteration_to_modify
    ])]
    
    messages = []
    for iteration in SelfDrivingTaskIteration.objects.filter(
            id__in=all_iteration_ids
    ).exclude(
        evaluation_json__isnull=True
    ).order_by("timestamp"):
        description = ""
        include_strategic_guidance = False
        if iteration == previous_iteration and previous_iteration != iteration_to_modify:
            description = "This is the evalutation of the execution of a previous iteration of the code"
        elif iteration == iteration_to_modify:
            description = "We are rolling the code back to this iteration. This is the evalutation of the execution of iteration of the code we are rolling back to.  We will start our new changes from this code"
            include_strategic_guidance = True
        else:
            description = "This is an evaluation previous iteration of the code.  It is not the previous iteration neither is it the iteration we are rolling back to"
        
        evaluation_json: dict = iteration.evaluation_json
        evaluation_parts = [
            evaluation_json.get("summary"),
        ]
        error_summary, error_logs = iteration.get_error()
        if error_summary:
            evaluation_parts.append(f"""
{error_summary}

# Log Output
{error_logs}
            """)
        else:
            asdf = 1
        
        iteration_data = {
            "iteration_id": iteration.id,
            "iteration_version_number": iteration.version_number,
            "iteration_description": description,
        }
        
        if not (error_summary or error_logs):
            iteration_data = {
                **iteration_data,
                "error_summary": "None.  No errors detected.  The code executed without error.",
            }
        
        if error_summary:
            iteration_data = {
                **iteration_data,
                "error_summary": error_summary,
            }
        
        if error_logs:
            iteration_data = {
                **iteration_data,
                "error_logs": error_logs,
            }
        
        if False and include_strategic_guidance:
            iteration_data['strategic_guidance'] = evaluation_json.get("strategic_guidance", "none")
        
        messages.append(iteration_data)
    
    return LlmMessage.user_from_data(
        title,
        messages,
        item_name="previous_iteration_analyses"
    )


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
        messages.append(msg)
    
    return LlmMessage.sys("\n\n-------\n\n".join(messages))


def get_architecture_docs(config: SelfDriverConfig):
    return LlmMessage.user_from_data(
        "Architecture", {
            "business_architecture": config.business.architecture,
            "initiative_architecture": config.initiative.architecture,
            "notes": "Business_architecture describes the whole picture for the business.  "
                     "Initiative_architecture describes details specific to this initiative.  "
                     "If there are conflicts between business_architecture and initiative_architecture, Business Architecture takes precedence."
        }
    )


def perform_code_review(
        config: SelfDriverConfig,
        planning_data
):
    current_iteration = config.current_iteration
    previous_iteration = config.previous_iteration
    iteration_to_modify = config.iteration_to_modify
    task = config.task
    
    messages = [
        get_sys_prompt(
            [
                "codereviewer.md",
                "common--credentials_architecture.md"
            ]
        ),
        *get_architecture_docs(config),
        *common.ensure_list(
            get_tombstone_message(config)
        ),
        # *common.ensure_list(
        #     get_relevant_code_files(config, current_iteration, iteration_to_modify)
        # ),
        *common.ensure_list(
            get_file_structure_msg(config.sandbox_root_dir) if not iteration_to_modify.has_error() else []
        ),
        *common.ensure_list(
            get_prev_attemp_summaries(config)
        ),
        *common.ensure_list(
            LlmMessage.user_from_data(
                "Relevant past lessons",
                get_lessons(config)
            )
        ),
        *common.ensure_list(
            config.guidance
        ),
        *common.ensure_list(
            LlmMessage.user_from_data(
                "Code Review Input: Proposed Code Changes for Current Iteration",
                [
                    cv.get_llm_message_data()
                    for cv in current_iteration.get_all_code_versions()
                ]
            )
        ),
        LlmMessage.user(f'''
The code changes to review are in support of the following goal:

# Goal
{task.description}

# Test Plan
{task.test_plan or 'none'}

# Risk Notes
{task.risk_notes or 'none'}
            '''),
        LlmMessage.user("Please perform the code review")
    ]
    
    code_review_data = llm_chat(
        "Perform Code Review",
        config,
        messages,
        LlmModel.OPENAI_GPT_5,
        output_schema="codereviewer.md.schema.json"
    ).json()
    
    config.log(code_review_data)
    
    blocking_issues = code_review_data.get("blocking_issues", [])
    non_blocking_warnings = code_review_data.get("non_blocking_warnings", [])
    if blocking_issues:
        raise CodeReviewException(code_review_data)
    elif non_blocking_warnings:
        config.log(non_blocking_warnings)


def get_prev_attemp_summaries(config: SelfDriverConfig, disabled=True) -> list[dict]:
    if disabled:
        return []
    
    attempt_count = 0
    
    previous_iterations: list[SelfDrivingTaskIteration] = list(
        config.self_driving_task
        .selfdrivingtaskiteration_set
        .filter(evaluation_json__isnull=False)
        .filter(planning_json__isnull=False)
        .exclude(id=config.current_iteration.pk)
        .order_by("-timestamp")
    )[0:10]
    
    previous_iteration_datas = [
        {
            "iteration_id": i.id,
            "timestamp": i.timestamp,
            "version": i.version_number,
            "coding_plan": i.planning_json.get("code_files"),
            "summary": i.evaluation_json.get("summary"),
            "error": i.evaluation_json.get("error")
        }
        for i in sorted(previous_iterations, key=lambda _i: _i.timestamp)
    ]
    
    return LlmMessage.user_from_data(
        f"Summaries from last {len(previous_iteration_datas)} code iterations(s).  The 'coding_plan' defines the coding plan that caused the error and resultant summary.  Treat these as lessons.  Do not repeat these errors",
        previous_iteration_datas
    )


def get_lessons(
        config,
        task_desc=None,
        all_lessons=True,
        exclude_invalid=True, skip=False
) -> list[dict]:
    if skip:
        return []
    
    if all_lessons:
        lessons_q = AgentLesson.objects.all()
    else:
        import numpy as np  # Ensure this is at the top of the file if not already imported
        # Load embedding model (should ideally be cached/shared elsewhere)
        query_embedding = sentence_transformer_model.encode(task_desc, normalize_embeddings=True)
        query_embedding = np.array(query_embedding).flatten().tolist()
        
        # Define a custom Func for pgvector cosine distance
        class CosineDistance(Func):
            function = ''
            template = '%(expressions)s <-> %s'
        
        # Query AgentLesson objects by vector similarity (cosine distance) using raw SQL to avoid Django's type system issues
        lessons_q = (
            AgentLesson.objects
            .extra(
                select={"distance": "embedding <-> %s::vector"},
                select_params=[query_embedding]
            ).extra(
                where=["embedding <-> %s::vector <= 0.3"],
                params=[query_embedding]
            )
            .order_by("distance")[:5]
        )
    
    if exclude_invalid:
        lessons_q = lessons_q.exclude(invalid_lesson=True)
    
    if task_desc:
        lessons_q = lessons_q.filter(agent_step=task_desc)
    
    lessons = list(lessons_q)
    
    if not all_lessons:
        # Deduplicate semantically similar lessons using embeddings
        import numpy as np
        from sentence_transformers import util
        
        # Generate text to embed
        lesson_texts = [f"{a.pattern}. {a.trigger}. {a.lesson}" for a in lessons]
        if lesson_texts:
            lesson_embeddings = sentence_transformer_model.encode(lesson_texts, normalize_embeddings=True)
        else:
            lesson_embeddings = []
        
        # Deduplicate by semantic similarity
        seen = []
        unique_indices = []
        
        for i, emb in enumerate(lesson_embeddings):
            if all(util.cos_sim(emb, lesson_embeddings[j]) < 0.92 for j in unique_indices):
                unique_indices.append(i)
        
        lessons = [lessons[i] for i in unique_indices]
    
    return {
        "important_quote": "Those who don't learn from history are doomed to repeat it",
        "lessons": [
            f"{a.lesson} - otherwise you'll see problems like this: {a.pattern} ({a.trigger})"
            for a in lessons
        ]
    }


def extract_lessons(
        config: SelfDriverConfig,
        agent_step: str,
        log_content,
        skip=True
):
    current_iteration = config.current_iteration
    task = config.task
    lessons_data = llm_chat(
        "Extract Lessons",
        config,
        [
            get_sys_prompt("lesson_extractor.md"),
            LlmMessage.user(task.get_work_desc()),
            *LlmMessage.user_from_data(f"Log Content from the '{agent_step}' step", log_content),
            *LlmMessage.user_from_data(
                "Existing Lessons (Don't repeat these)",
                get_lessons(config, exclude_invalid=False)
            )
        ],
        output_schema="lesson_extractor.md.schema.json",
        model=LlmModel.OPENAI_GPT_5
    ).json()
    
    for lesson_data in common.ensure_list(common.get(lessons_data, "lessons", [])):
        AgentLesson.create_from_data(
            agent_step,
            lesson_data,
            current_iteration
        )


def implement_code_changes(
        config: SelfDriverConfig,
        planning_data: dict,
        code_review_exception: CodeReviewException
) -> SelfDrivingTaskIteration:
    current_iteration = config.current_iteration
    previous_iteration = config.previous_iteration
    iteration_to_modify = config.iteration_to_modify
    code_file_instructions = planning_data.get("code_files", [])
    if not code_file_instructions:
        raise BadPlan("no code files found", planning_data)
    
    if code_review_exception:
        code_review_file_blockers, code_review_file_warnings = code_review_exception.get_issue_dicts()
    else:
        code_review_file_blockers = code_review_file_warnings = defaultdict(list)
    
    code_file_instructions = (
            [cfi for cfi in code_file_instructions if cfi.get("code_file_path") == "requirements.txt"]
            +
            [cfi for cfi in code_file_instructions if cfi.get("code_file_path") != "requirements.txt"]
    )
    
    if previous_iteration and (previous_iteration != iteration_to_modify):
        roll_back_reason = planning_data.get("rollback_reason")
    else:
        roll_back_reason = None
    
    requirements_txt = CodeFile.get(config.business, "requirements.txt").get_latest_version().code
    
    for cfi in code_file_instructions:
        code_file_path_str: str = cfi.get("code_file_path")
        if code_file_path_str.startswith("/"):
            raise BadPlan(f"invalid file path: {code_file_path_str} - code file paths are forbidden from starting with a slash", planning_data)
        
        if code_file_path_str.startswith(str(config.sandbox_root_dir)):
            code_file_path_str = code_file_path_str[len(str(config.sandbox_root_dir)) + 1:]
        
        blocking_issues = code_review_file_blockers[code_file_path_str]
        if code_review_exception and not blocking_issues:
            # we are fixing a codereview exception, but no changes to this file
            continue
        
        non_blocking_issues = code_review_file_warnings[code_file_path_str]
        
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
        dsl_instructions = cfi.get("dsl_instructions", [])
        if not (instructions or dsl_instructions):
            config.log(f"no modifications for {code_file_path}")
            code_file.update(
                current_iteration,
                code_version_to_modify.code
            )
        else:
            previous_exception = None
            code_str = None
            for i in range(3):
                try:
                    code_str = write_code_file(
                        config=config,
                        code_version_to_modify=code_version_to_modify,
                        code_file_data=cfi,
                        requirements_txt=requirements_txt,
                        blocking_issues=blocking_issues,
                        code_writing_model=LlmModel.OPENAI_GPT_5,  # LlmModel(cfi.get("code_writing_model")),
                        roll_back_reason=roll_back_reason,
                        previous_exception=previous_exception
                    )
                    
                    previous_exception = None
                    break
                except CodeCompilationError as e:
                    extract_lessons(
                        config,
                        TASK_DESC_CODE_WRITING,
                        f"""
the code written for {code_version_to_modify.code_file.file_path}:
'''
{e.code_str}
'''

written by following these instructions:
'''
{instructions}
'''

resulted in this validation error
'''
{traceback.format_exc()}
'''
                        """
                    )
                    previous_exception = e
            
            if previous_exception:
                # validation failed three times.  keep going, if it fails in deployment or execution we'll have another 
                # chances at the feedback loop
                config.log(previous_exception)
            
            if code_str:
                code_file.update(
                    current_iteration,
                    code_str,
                    code_instructions=instructions
                )
                if code_file_path_str == "requirements.txt":
                    requirements_txt = code_str
    
    config.git.add_files()
    return current_iteration


def post_process_code_ouput(code_str: str, code_version_to_modify: CodeVersion) -> str:
    if not code_str:
        return code_str
    
    code_file_path_str = code_version_to_modify.code_file.get_path()
    
    # FULL_FILE: <path> header support
    stripped = code_str.lstrip()
    if stripped.startswith('FULL_FILE:'):
        # Drop the header line and keep the remainder as the file contents
        newline_idx = stripped.find('\n')
        code_str = '' if newline_idx == -1 else stripped[newline_idx + 1:]
    elif codegen_utils.looks_like_unified_diff(code_str):
        try:
            # Apply patch against the current version of the file
            code_str = codegen_utils.apply_unified_diff_to_text(
                code_version_to_modify.code,
                code_str
            )
        except Exception as patch_e:
            # Surface a structured error that preserves context for lesson extraction
            raise CodeCompilationError(
                code_version_to_modify.code,
                f"Failed to apply git patch to {code_file_path_str}: {patch_e}"
            )
    return code_str


def write_initial_test(config: SelfDriverConfig):
    task = config.task
    
    previous_exception = None
    
    for i in range(3):
        try:
            messages = [
                get_sys_prompt([
                    "codewriter--python_test.md",
                    "codewriter--initial_test.md",
                    "codewriter--common.md",
                    "common--iam_role.md",
                    "common--llm_chat.md",
                    "common--credentials_architecture.md",
                    "common--forbidden_actions.md",
                    "common--environment_variables.md"
                ], replacements=[
                    ("<env_vars>", get_env_var_names(config)),
                    ("<business_tag>", config.business.service_token),
                ]),
                *LlmMessage.user_from_data(
                    "**Please write a single file, comprensive test suite that asserts this behavior.  This test suite will be used for Test Driven Development**",
                    {
                        "GOAL": task.description,
                        "test_plan": task.test_plan,
                        "risk_notes": task.risk_notes
                    }
                )
            ]
            
            if config.self_driving_task.test_file_path and (config.sandbox_root_dir / config.self_driving_task.test_file_path).exists():
                messages += LlmMessage.user_from_data(
                    "current version of the test code",
                    config.sandbox_root_dir / config.self_driving_task.test_file_path
                )
            
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
                LlmModel.OPENAI_GPT_5,
                reasoning_effort=LlmReasoningEffort.HIGH
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
                        config,
                        test_file_path,
                        code
                    )
                    break
                except CodeCompilationError as code_compilation_error:
                    config.log(f"Code failed validation. Attempting fix using cheaper model.  Fix attempt {code_validation_idx + 1} of 5")
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
                code_verson = config.current_iteration.get_code_version(test_file_path)
                SelfDrivingTask.objects.filter(id=config.self_driving_task.id).update(
                    test_file_path=test_file_path.relative_to(config.sandbox_root_dir)
                )
                config.self_driving_task.refresh_from_db(fields=["test_file_path"])
            
            return test_file_path
        except Exception as e:
            config.log(e)
            previous_exception = e
    
    raise previous_exception


def identify_required_credentials(
        config: SelfDriverConfig
):
    task = config.task
    
    planning_data = llm_chat(
        "Identify required credentials",
        config,
        [
            *common.ensure_list(
                get_sys_prompt([
                    "codeplanner--initial_credentials.md",
                    "codeplanner--common.md",
                    "common--llm_chat.md",
                    "common--iam_role.md",
                    "common--forbidden_actions.md",
                    "common--environment_variables.md",
                    "common--credentials_architecture.md"
                ], replacements=[
                    ("<credential_manager_existing_services>", credential_manager.get_existing_service_names_desc()),
                    ("<business_tag>", config.business.service_token),
                    ("<env_vars>", get_env_var_names(config)),
                    ("<credential_manager_existing_service_schemas>", credential_manager.get_existing_service_schema_desc()),
                    get_readonly_files_replacement(config)
                ]),
            ),
            *get_architecture_docs(config),
            *get_existing_required_credentials(config),
            "Please identify the credentials required"
        ],
        config.model_code_planning,
        reasoning_effort=LlmReasoningEffort.HIGH,
        output_schema="codeplanner.schema.json"
    ).json()
    
    current_credentials = config.business.required_credentials or {}
    Business.objects.filter(id=config.business.id).update(
        required_credentials={
            **current_credentials,
            **(planning_data["required_credentials"] or {})
        }
    )
    config.business.refresh_from_db(fields=["required_credentials"])


def write_business_architecture(config: SelfDriverConfig):
    business_architecture = llm_chat(
        "Write initial design doc",
        config,
        [
            get_sys_prompt(
                [
                    "system_architect.md",
                    "common--llm_chat.md",
                    "common--iam_role.md",
                    "common--forbidden_actions.md",
                    "common--environment_variables.md",
                    "common--infrastructure_rules.md",
                    "common--credentials_architecture.md"
                ],
                replacements=[
                    ("<env_vars>", get_env_var_names(config)),
                    get_readonly_files_replacement(config)
                ]
            ),
            *LlmMessage.user_from_data("Business Description", {
                "business_description": config.business.llm_data()
            }),
            *LlmMessage.user_from_data(
                "Business Initiatives", [i.llm_data() for i in config.business.initiative_set.all()], "planned_initiatives"
            ),
            "Please write a markdown-formatted high-level design document for Business's architecture"
        ],
        model=LlmModel.OPENAI_GPT_5,
        reasoning_effort=LlmReasoningEffort.HIGH,
        verbosity=LlmVerbosity.MEDIUM
    ).text
    
    Business.objects.filter(id=config.business.id).update(
        architecture=business_architecture
    )
    config.business.refresh_from_db(fields=["architecture"])


def write_initiative_architecture(config: SelfDriverConfig):
    architecture = llm_chat(
        "Write Initiative design doc",
        config,
        [
            get_sys_prompt(
                [
                    "system_architect.md",
                    "common--llm_chat.md",
                    "common--iam_role.md",
                    "common--forbidden_actions.md",
                    "common--environment_variables.md",
                    "common--infrastructure_rules.md",
                    "common--credentials_architecture.md"
                ],
                replacements=[
                    ("<env_vars>", get_env_var_names(config)),
                    get_readonly_files_replacement(config)
                ]
            ),
            *LlmMessage.user_from_data("Full Business Architecture", {
                "architecture_docs": config.business.architecture
            }),
            *LlmMessage.user_from_data(
                "Current Initiative", config.initiative.llm_data()
            ),
            "Please write a markdown-formatted high-level design document for this **Initiatives's** architecture. It should **never conflict** with the supplied business's architecture - it should only clarify details needed to implement the Current Initiative."
        ],
        model=LlmModel.OPENAI_GPT_5,
        reasoning_effort=LlmReasoningEffort.HIGH,
        verbosity=LlmVerbosity.MEDIUM
    ).text
    
    Initiative.objects.filter(id=config.initiative.id).update(
        architecture=architecture
    )
    config.initiative.refresh_from_db(fields=["architecture"])


def update_file_contents(
        config: SelfDriverConfig,
        file_path: Path,
        code: str
):
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(code)
    with transaction.atomic():
        code_verson = config.current_iteration.get_code_version(file_path)
    return file_path


def get_tombstone_message(config, disabled=True) -> list[LlmMessage]:
    asdf = LlmMessage.user_from_data(
        "deprecation_plan", [
            t.data_json for t in AgentTombstone.objects.filter(business=config.business)
        ],
        item_name="tombstone"
    )
    
    return asdf


def get_budget_message(config) -> LlmMessage:
    iteration_count = config.self_driving_task.selfdrivingtaskiteration_set.count()
    
    return LlmMessage.user(f"""
## Budget Information
This is your attempt number {iteration_count + 1} on this Task

You've spent ${config.self_driving_task.get_cost() :.2f} USD out of a max budget of ${config.budget :.2f} USD
    """)


def route_code_changes(config: SelfDriverConfig) -> DevelopmentRoutingPath:
    business = config.self_driving_task.business
    current_iteration = config.current_iteration
    previous_iteration = config.previous_iteration
    iteration_to_modify = config.iteration_to_modify
    
    if not iteration_to_modify.has_error():
        return DevelopmentRoutingPath.ESCALATE_TO_PLANNER
    else:
        error_summary, error_logs = iteration_to_modify.get_error()
        routing_data = llm_chat(
            "Identify Development Route",
            config,
            [
                *common.ensure_list(
                    get_sys_prompt(
                        [
                            "failure_router.md",
                            "common--iam_role.md",
                            "common--forbidden_actions.md",
                            "common--credentials_architecture.md",
                            "common--environment_variables.md"
                        ],
                        replacements=[
                            ("<env_vars>", get_env_var_names(config)),
                            get_readonly_files_replacement(config)
                        ]
                    ),
                ),
                *get_architecture_docs(config),
                *common.ensure_list(
                    LlmMessage.user_from_data(
                        "Error with previous code iteration",
                        {
                            "summary": error_summary,
                            "logs": error_logs
                        }
                    )
                ),
                *common.ensure_list(
                    get_tombstone_message(config)
                ),
                *common.ensure_list(
                    get_prev_attemp_summaries(config)
                ),
                *common.ensure_list(
                    LlmMessage.user_from_data(
                        "Do not repeat these mistakes - before you respond, checklist each item to make sure you're not repeating it",
                        get_lessons(config)
                    )
                ),
                *common.ensure_list(
                    get_dependencies_msg(config, for_planning=True)
                ),
                "Please perform the routing analysis"
            ],
            LlmModel.OPENAI_GPT_5,
            output_schema="failure_router.md.schema.json"
        ).json()
        
        SelfDrivingTaskIteration.objects.filter(id=current_iteration.id).update(
            routing_json=routing_data
        )
        current_iteration.refresh_from_db(fields=["routing_json"])
        
        return DevelopmentRoutingPath(routing_data.get("recovery_path"))


def plan_aws_provisioning_code_changes(config: SelfDriverConfig):
    current_iteration = config.current_iteration
    previous_iteration = config.previous_iteration
    iteration_to_modify = config.iteration_to_modify
    routing_json = current_iteration.routing_json
    
    context_files = routing_json.get("context_files", []) + [
        "Dockerfile",
        "infrastructure.yaml"
    ]
    
    planning_data = llm_chat(
        "Plan aws provisioning code changes",
        config,
        [
            *common.ensure_list(
                get_sys_prompt(
                    [
                        "codeplanner--aws_provisioning.md",
                        "codeplanner--common.md",
                        "common--llm_chat.md",
                        "common--iam_role.md",
                        "common--forbidden_actions.md",
                        "common--environment_variables.md",
                        "common--infrastructure_rules.md",
                        "common--credentials_architecture.md"
                    ], replacements=[
                        ("<business_tag>", config.business.service_token),
                        ("<credential_manager_existing_services>", credential_manager.get_existing_service_names_desc()),
                        ("<credential_manager_existing_service_schemas>", credential_manager.get_existing_service_schema_desc()),
                        ("<env_vars>", get_env_var_names(config)),
                        ("<stack_name_dev>", config.self_driving_task.get_cloudformation_stack_name(AwsEnv.DEV)),
                        ("<stack_name_prod>", config.self_driving_task.get_cloudformation_stack_name(AwsEnv.PRODUCTION)),
                        get_readonly_files_replacement(config)
                    ]
                ),
            ),
            *get_architecture_docs(config),
            *get_existing_required_credentials(config),
            *common.ensure_list(
                get_prev_attemp_summaries(config)
            ),
            *common.ensure_list(
                LlmMessage.user_from_data(
                    "Do not repeat these mistakes - before you respond, checklist each item to make sure you're not repeating it",
                    get_lessons(config)
                )
            ),
            *common.ensure_list(
                build_cloudformation_durations_context_messages(config)
            ),
            *common.ensure_list(
                build_previous_iteration_context_messages(config, title="structured error reports")
            ),
            *common.ensure_list(
                get_relevant_code_files(config, context_files)
            ),
            *common.ensure_list(
                LlmMessage.user_from_data("structured failure triage object", routing_json)
            ),
            "Please produce a development plan that addresses this issue"
        
        ],
        config.model_code_planning,
        reasoning_effort=LlmReasoningEffort.HIGH,
        output_schema="codeplanner.schema.json"
    ).json()
    
    with transaction.atomic():
        SelfDrivingTaskIteration.objects.filter(id=current_iteration.id).update(
            planning_model=config.model_code_planning
        )
        current_iteration.refresh_from_db(fields=["planning_model"])
    
    blocked_data = planning_data.get('blocked')
    if blocked_data:
        raise AgentBlocked(blocked_data)
    
    return planning_data


def get_readonly_files_paths(config: SelfDriverConfig) -> list[str]:
    readonly_file_paths = READONLY_FILES
    
    already_there = []
    
    for test_file in config.business.codefile_set.filter(
            Q(file_path__contains="test/") | Q(file_path__contains="/test")
    ).exclude(
        file_path=config.self_driving_task.test_file_path
    ):
        if test_file.file_path in already_there:
            continue
        
        already_there.append(test_file.file_path)
        readonly_file_paths.append(
            {
                "path": test_file.file_path,
                "alternatives": config.self_driving_task.test_file_path,
                "description": "This is an existing test that asserts another tasks behavior.  This test must never be modifified.  If this test is failing, that means the code you wrote for this task caused a regression"
            }
        )
    
    return readonly_file_paths


def get_readonly_files_replacement(config: SelfDriverConfig) -> tuple[str, str]:
    parts = []
    
    for f in get_readonly_files_paths(config):
        parts.append(f"- `{f['path']}` — {f['description']}. If you believe a change is needed to {f['path']}, the change likely belongs in `{f['alternatives']}` instead")
    
    return "<read_only_files>", "\n".join(parts)


def plan_direct_fix_code_changes(config: SelfDriverConfig):
    current_iteration = config.current_iteration
    previous_iteration = config.previous_iteration
    iteration_to_modify = config.iteration_to_modify
    routing_json = current_iteration.routing_json
    
    planning_data = llm_chat(
        "Plan quick fix code changes",
        config,
        [
            *common.ensure_list(
                get_sys_prompt([
                    "codeplanner--quick_fix.md",
                    "codeplanner--common.md",
                    "common--llm_chat.md",
                    "common--iam_role.md",
                    "common--forbidden_actions.md",
                    "common--environment_variables.md",
                    "common--credentials_architecture.md",
                    "common--infrastructure_rules.md"
                ], replacements=[
                    ("<business_tag>", config.business.service_token),
                    ("<credential_manager_existing_services>", credential_manager.get_existing_service_names_desc()),
                    ("<env_vars>", get_env_var_names(config)),
                    ("<credential_manager_existing_service_schemas>", credential_manager.get_existing_service_schema_desc()),
                    get_readonly_files_replacement(config)
                ]),
            ),
            *get_architecture_docs(config),
            *get_existing_required_credentials(config),
            *common.ensure_list(
                get_prev_attemp_summaries(config)
            ),
            *common.ensure_list(
                LlmMessage.user_from_data(
                    "Do not repeat these mistakes - before you respond, checklist each item to make sure you're not repeating it",
                    get_lessons(config)
                )
            ),
            *common.ensure_list(
                build_cloudformation_durations_context_messages(config)
            ),
            *common.ensure_list(
                build_previous_iteration_context_messages(config, title="structured error reports")),
            *common.ensure_list(
                get_relevant_code_files(config, routing_json.get("context_files", []))
            ),
            *common.ensure_list(
                LlmMessage.user_from_data("structured failure triage object", routing_json)
            ),
            "Please produce a development plan that addresses this issue"
        ],
        config.model_code_planning,
        reasoning_effort=LlmReasoningEffort.HIGH,
        output_schema="codeplanner.schema.json"
    ).json()
    
    with transaction.atomic():
        SelfDrivingTaskIteration.objects.filter(id=current_iteration.id).update(
            planning_model=config.model_code_planning
        )
        current_iteration.refresh_from_db(fields=["planning_model"])
    
    blocked_data = planning_data.get('blocked')
    if blocked_data:
        raise AgentBlocked(blocked_data)
    
    return planning_data


def plan_full_code_changes(config: SelfDriverConfig):
    current_iteration = config.current_iteration
    previous_iteration = config.previous_iteration
    iteration_to_modify = config.iteration_to_modify
    
    relevant_code_files = get_relevant_code_files(config)
    
    business = config.self_driving_task.business
    
    task = config.self_driving_task.task
    task_type = TaskType(task.task_type)
    
    system_prompt_files = [
        MAP_TASKTYPE_TO_PLANNING_PROMPT[task_type],
        "codeplanner--full_plan_base.md",
        "codeplanner--common.md",
        "common--llm_chat.md",
        "common--iam_role.md",
        "common--forbidden_actions.md",
        "common--credentials_architecture.md",
        "common--environment_variables.md",
        "common--infrastructure_rules.md"
    ]
    
    if config.self_driving_task.test_file_path:
        system_prompt_files.append(
            "codeplanner--test_driven_development.md"
        )
    
    messages = [
        get_sys_prompt(
            system_prompt_files,
            [
                ("<business_tag>", config.business.service_token),
                ("<credential_manager_existing_services>", credential_manager.get_existing_service_names_desc()),
                ("<credential_manager_existing_service_schemas>", credential_manager.get_existing_service_schema_desc()),
                ("<test_file_path>", str(config.self_driving_task.test_file_path or "")),
                ("<env_vars>", get_env_var_names(config)),
                ("<aws_tag>", str(business.service_token)),
                ("<db_name>", str(business.service_token)),
                ("<iam_role_name>", str(business.get_iam_role_name())),
                ("<artifacts_directory>", str(config.artifacts_dir)),
                ("<sandbox_dir>", str(config.sandbox_root_dir)),
                get_readonly_files_replacement(config)
            ]
        ),
        *get_architecture_docs(config),
        *get_existing_required_credentials(config),
        *common.ensure_list(
            get_budget_message(config)
        ),
        *common.ensure_list(
            build_cloudformation_durations_context_messages(config)
        ),
        *common.ensure_list(
            get_tombstone_message(config)
        ),
        *common.ensure_list(
            build_previous_iteration_context_messages(config)
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
            get_file_structure_msg(config.sandbox_root_dir) if not iteration_to_modify.has_error() else []
        ),
        *common.ensure_list(
            config.guidance
        ),
        *common.ensure_list(
            get_prev_attemp_summaries(config)
        ),
        *common.ensure_list(
            LlmMessage.user_from_data(
                "Do not repeat these mistakes - before you respond, checklist each item to make sure you're not repeating it",
                get_lessons(config)
            )
        ),
        *common.ensure_list(
            LlmMessage.user(f"""
The previous iteration failed at the deployment stage.   

**Application level code changes are FORBIDDEN at this point, and will be FORBIDDEN until the deployment is fixed**
- Any application level code changes at this point would be purely speculative and not based on an execution feedback loop
- You may only plan changes for environment /  infrastructure files (Dockerfile, cloudformation configs (infrastructure.yaml), requirements.txt, etc)

**YOUR PRIMARY OBJECTIVE AT THIS POINT IS TO FIX THE DEPLOYMENT PROBLEM**
            """)
            if iteration_to_modify.has_error() else get_goal_msg(config)
        )
    ]
    
    planning_data = llm_chat(
        "Plan code changes",
        config,
        messages,
        config.model_code_planning,
        reasoning_effort=LlmReasoningEffort.HIGH,
        output_schema="codeplanner.schema.json"
    ).json()
    
    with transaction.atomic():
        SelfDrivingTaskIteration.objects.filter(id=current_iteration.id).update(
            planning_model=config.model_code_planning,
            execute_module=planning_data.get('execute_module'),
            test_module=planning_data.get('test_module')
        )
        current_iteration.refresh_from_db(fields=["planning_model", "execute_module", "test_module"])
    
    blocked_data = planning_data.get('blocked')
    if blocked_data:
        raise AgentBlocked(blocked_data)
    
    return planning_data


def write_code_file(
        config: SelfDriverConfig,
        code_version_to_modify: CodeVersion,
        code_file_data: dict,
        code_writing_model: LlmModel,
        requirements_txt: str,
        blocking_issues: list[dict],
        roll_back_reason: str = None,
        previous_exception: Optional[CodeCompilationError] = None
) -> str:
    # instruction = "\n".join([i.get("details") for i in instructions])
    
    current_iteration = config.current_iteration
    previous_iteration = config.previous_iteration
    iteration_to_modify = config.iteration_to_modify
    
    code_file = code_version_to_modify.code_file
    code_file_path = code_file.get_path()
    code_file_name = code_file_path.name
    logging.info(f"writing code: {code_file_name}")
    
    messages: list[LlmMessage] = [
        get_sys_prompt(
            [
                *get_codewriter_system_prompt(code_file_path),
                "codewriter--common.md",
                "common--iam_role.md",
                "common--credentials_architecture.md",
                "common--forbidden_actions.md"
            ],
            replacements=[
                ("<business_tag>", config.business.service_token),
                ("<sandbox_dir>", str(config.sandbox_root_dir)),
                ("<env_vars>", get_env_var_names(config))
            ]
        ),
        *get_architecture_docs(config),
        *build_previous_iteration_context_messages(
            config,
            title="previous iteration evaluations - learn from these past attempts. **you must not repeat these errors**"
        ),
        *common.ensure_list(
            get_tombstone_message(config)
        ),
        *common.ensure_list(
            LlmMessage.sys(
                "## Forbidden Actions\n• You **MUST NEVER** wrap the code in Markdown-style code fences such as ```<filetype>. Output must be raw code syntax only.")
            if not code_file_name.endswith(".md") else []
        )
    ]
    if code_file_name == "infrastructure.yaml":
        messages += build_cloudformation_durations_context_messages(config)
    
    related_code_file_versions = []
    for cfp in code_file_data.get("related_code_file_paths", []):
        if not CodeFile.objects.filter(business=config.business, file_path=cfp).exists():
            config.log(f"ERROR: related_code_file_path {cfp} does not exist")
            continue
        
        if cfp == code_file_data.get("code_file_path"):
            config.log(f"ERROR: related_code_file_path {cfp} is the same as the file to be edited")
            continue
        
        version = CodeFile.get(business=config.business, code_file_path=cfp).get_version(current_iteration, default_to_latest=True)
        if not version:
            config.log(f"ERROR: not version for {cfp} exists")
            continue
        
        related_code_file_versions.append(
            version.get_llm_message_data()
        )
    
    if related_code_file_versions:
        messages += LlmMessage.user_from_data(
            title="Related Code File Context",
            data=related_code_file_versions,
            item_name="related_code_files"
        )
    
    if code_file_name.endswith(".py"):
        messages += get_requirementstxt_msg(requirements_txt)
    
    code_versions = {}
    if previous_iteration:
        messages += get_iteration_eval_llm_messages(
            config,
            previous_iteration
        )
        
        previous_iteration_version = code_file.get_version(previous_iteration)
        if previous_iteration_version:
            code_versions[previous_iteration_version.id] = (
                "Previous Iteration's Code (your previous attempt at writing this code)",
                previous_iteration_version
            )
    
    if code_version_to_modify and code_version_to_modify.code:
        current_version_title = f"Contents of {code_file.file_path}.  THIS IS CODE YOU WILL MODIFY.  "
        if roll_back_reason:
            current_version_title += f"We rolled back to a previous version and are editing this rolled back version because of the following reason:\n'''\n{roll_back_reason}\n'''"
        
        code_versions[code_version_to_modify.id] = (
            current_version_title,
            code_version_to_modify
        )
    
    messages += LlmMessage.user_from_data("Code Files", {
        "code_file_versions": [
            {
                "file_description": title,
                **code_version.get_llm_message_data()
            } for title, code_version in code_versions.values() if code_version.code
        ]
    })
    
    fix_prompt = common.get(current_iteration, ["routing_json", "fix_prompt"])
    if fix_prompt:
        messages.append(LlmMessage.user(f"suggested prompt to assist in fixing the issue:  {fix_prompt}"))
    
    coding_task_data = {
        "guidance": code_file_data.get("guidance"),
    }
    
    if "dsl_instructions" in code_file_data:
        coding_task_data["dsl_instructions"] = code_file_data.get("dsl_instructions")
    elif "instructions" in code_file_data:
        coding_task_data["instructions"] = code_file_data.get("instructions")
    else:
        raise Exception(f"no instructions in {json.dumps(coding_task_data, indent=4)}")
    
    lessons = get_lessons(config, task_desc=TASK_DESC_CODE_WRITING)
    if lessons:
        coding_task_data["previously_learned_lessons"] = {
            "description": "Lessons learned in previous iterations.  Do not repeat these mistakes - before you respond, checklist each item to make sure you're not repeating it",
            **lessons
        }
    
    if blocking_issues:
        coding_task_data["code_review_errors"] = {
            "description": "**Code Review Failure** Your previous attempt to generate code based on the instruction set resulted in these blocking code review errors.  **You must** avoid these errors in the next version of the code",
            "blocking_codereview_errors": str(blocking_issues)
        }
    
    if previous_exception:
        coding_task_data["code_validation_errors"] = {
            "description": "**Code Validation Failure** Your previous attempt to generate code based on the instruction set resulted in these validation errors.  **You must** avoid these errors in the next version of the code",
            "error_log": str(previous_exception)
        }
    
    messages += LlmMessage.user_from_data(
        f"**YOUR ONE AND ONLY TASK:**\n{'Modify' if code_version_to_modify.code else 'Write the initial version of'} {code_file.file_path}, following each of these instruction steps exactly and in order, while avoiding repeating any previous errors or blocking_codereview_errors (if applicable) and applying the applicable lessons learned from previous attempts.",
        coding_task_data
    )
    
    code = llm_chat(
        f"Write code for {code_file_name}",
        config,
        messages,
        code_writing_model,
        verbosity=LlmVerbosity.LOW,
        reasoning_effort=LlmReasoningEffort.HIGH
    ).text
    
    # Detect and handle git patch vs full-file outputs from the code writer
    code = post_process_code_ouput(
        code,
        code_version_to_modify
    )
    
    for i in range(5):
        try:
            return validate_code(
                config,
                code_file_path,
                code
            )
        except CodeCompilationError as code_compilation_error:
            config.log(f"Primary code failed validation. Attempting fix using cheaper model.  Fix attempt {i + 1} of 5")
            if i == 4:
                raise code_compilation_error
            else:
                code = fix_code_compilation(
                    config,
                    code_file_path,
                    code,
                    code_compilation_error
                )


def get_requirementstxt_msg(requirements_txt, header="The python environment has the following packages installed.  The code you write may only import from packages listed here") -> list[LlmMessage]:
    return LlmMessage.user_from_data(
        header,
        {
            "file_path": "requirements.txt",
            "code": requirements_txt
        }
    )


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
        model=LlmModel.OPENAI_GPT_5_MINI,
        code_response=True
    ).text
    return code


def get_codewriter_system_prompt(code_file_path) -> list[str]:
    code_file_name = code_file_path.name
    code_file_name_lower = code_file_name.lower()
    if code_file_name_lower in ["requirements.txt", "constraints.txt"]:
        prompt = "codewriter--requirements.txt.md"
    elif code_file_name_lower.startswith("test") and code_file_name_lower.endswith(".py"):
        prompt = [
            "codewriter--python_test.md",
            "common--llm_chat.md"
        ]
    elif code_file_name_lower == "settings.py":
        prompt = [
            "codewriter--django_settings.md",
            "codewriter--python_coder.md"
        ]
    elif code_file_name_lower.endswith(".py"):
        prompt = [
            "codewriter--python_coder.md",
            "common--llm_chat.md"
        ]
    elif code_file_name_lower.endswith(".json"):
        prompt = "codewriter--json_coder.md"
    elif code_file_name_lower.endswith(".eml"):
        prompt = "codewriter--eml_coder.md"
    elif code_file_name_lower.endswith(".md"):
        prompt = "codewriter--documentation_writer.md"
    elif code_file_name == "infrastructure.yaml":
        prompt = [
            "codewriter--aws_cloudformation_coder.md",
            "common--infrastructure_rules.md"
        ]
    elif code_file_name.startswith("Dockerfile"):
        prompt = "codewriter--dockerfile_coder.md"
    elif code_file_name_lower.endswith(".sql"):
        prompt = "codewriter--sql_coder.md"
    elif code_file_name_lower.endswith(".js"):
        prompt = "codewriter--javascript_coder.md"
    elif code_file_name_lower.endswith(".html"):
        prompt = "codewriter--html_coder.md"
    elif code_file_name_lower.endswith(".yaml"):
        prompt = "codewriter--yaml_coder.md"
    elif code_file_name_lower.endswith(".css"):
        prompt = "codewriter--css_coder.md"
    elif code_file_name_lower.endswith(".txt"):
        prompt = "codewriter--txt.md"
    elif code_file_name_lower.endswith(".ini"):
        prompt = "codewriter--ini.md"
    elif code_file_name_lower.startswith(".env"):
        raise BadPlan("All env vars must be fetched from the os environment.  Do not use .env files or decouple")
    else:
        raise AgentBlocked(f"no coder implemented for {code_file_name}.  Need JJ or a human to implement it in the Erie Iron agent codebase.")
    
    return common.ensure_list(prompt)


def validate_code(
        config: SelfDriverConfig,
        code_file_path: Path,
        code: str
) -> str:
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
    
    elif "Dockerfile" in code_file_name:
        validate_dockerfile(
            config.sandbox_root_dir,
            code_file_name
        )
    
    elif code_file_name == "infrastructure.yaml":
        try:
            data = yaml.load(code, Loader=yaml.BaseLoader)
            if not data.get("Parameters"):
                raise Exception(f"infrastructure.yaml lacks parameters")
        except Exception as e:
            raise CodeCompilationError(code, f"infrastructure.yaml parse error:\n{e}")
    
    elif code_file_name.endswith(".yaml"):
        try:
            data = yaml.load(code, Loader=yaml.BaseLoader)
        except Exception as e:
            raise CodeCompilationError(code, f"yaml parse error:\n{e}")
    
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


def get_dependencies_msg(config: SelfDriverConfig, for_planning: bool) -> list[LlmMessage]:
    header = "The python environment has the following packages installed"
    if for_planning:
        header += ".  If you need additional packages, you'll need to add them to the requirements.txt"
    
    return get_requirementstxt_msg(
        (config.sandbox_root_dir / 'requirements.txt').read_text(),
        header
    )


def get_docs_msg(config) -> list[LlmMessage]:
    def _load_markdown_doc(path: Path) -> Optional[LlmMessage]:
        if path.exists() and path.is_file():
            return None
    
    files = []
    readme_path = config.sandbox_root_dir / "README.md"
    if readme_path.exists():
        files.append(readme_path)
    
    docs_dir = config.sandbox_root_dir / "docs"
    if docs_dir.exists():
        for md_file in docs_dir.glob("*.md"):
            files.append({
                "file_path": md_file,
                "contents": md_file.read_text()
            })
    
    return LlmMessage.user_from_data(
        "The following are markdown documentation file(s). They may contain high-level design notes, architecture explanations, or usage instructions useful to developers and future planning agents.",
        files
    )


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


def get_existing_required_credentials(
        config: SelfDriverConfig
) -> list[LlmMessage]:
    return LlmMessage.user_from_data("Existing Required Credentials.  Use for reference.  Not need to re-specify.", {
        "required_credentials": config.business.required_credentials or {}
    })


def get_relevant_code_files(
        config: SelfDriverConfig,
        paths: list = None
) -> list[LlmMessage]:
    current_iteration = config.current_iteration
    iteration_to_modify = config.iteration_to_modify
    files = []
    
    if paths is not None:
        paths = common.strings(common.filter_none(paths))
        for path in set(paths):
            if path.startswith("/"):
                absolute_path = Path(path)
            else:
                absolute_path = config.sandbox_root_dir / path
            
            if not absolute_path.exists():
                logging.info(f"{absolute_path} does not exist, skipping")
                continue
            
            relative_path = absolute_path.relative_to(config.sandbox_root_dir)
            code_file = CodeFile.get(
                config.business,
                relative_path
            )
            
            code_version = code_file.get_version(iteration_to_modify, default_to_latest=True)
            if not code_version:
                try:
                    code_version = CodeFile.init_from_codefile(current_iteration, relative_path)
                except Exception as e:
                    config.log(f"failed to fetch {relative_path}")
                    config.log(e)
                    continue
            
            if code_version and code_version.code:
                files.append(code_version.get_llm_message_data())
    else:
        required_files = [
            config.self_driving_task.test_file_path,
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
                files.append(code_version.get_llm_message_data())
        
        # Step 1: Get the structured retrieval cues from the LLM
        cues = llm_chat(
            "Find relavant code",
            config,
            [
                get_sys_prompt("codefinder.md"),
                LlmMessage.user(config.self_driving_task.task.get_work_desc())
            ],
            LlmModel.OPENAI_GPT_5_MINI,
            output_schema="codefinder.md.schema.json",
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
            files.append({
                "file_path": code_file.file_path,
                "source": "imported_package",
                "code": grouped_code,
                "note": f"The following methods from an imported package may be referenced but not modified: {[m.name for m in methods]}"
            })
        
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
                files.append(code_version.get_llm_message_data())
    
    files_unique = {
        f.get("file_path"): f
        for f in files
    }
    return LlmMessage.user_from_data("Relevant Code Files", files_unique.values())


def llm_chat(
        desc: str,
        config: SelfDriverConfig,
        messages: list[LlmMessage],
        model: LlmModel,
        output_schema: Path = None,
        reasoning_effort: LlmReasoningEffort = None,
        verbosity: LlmVerbosity = None,
        code_response=False
) -> LlmResponse:
    if output_schema:
        logging.info(f"llm chat: {desc} ({output_schema})")
    else:
        logging.info(f"llm chat: {desc}")
    
    token_count = LlmMessage.get_total_token_count(model, messages)
    
    llm_resp = None
    for i in range(2):
        llm_messages = LlmMessage.parse_prompt(model, messages, code_response=code_response)
        
        llm_request = LlmRequest.objects.create(
            title=desc,
            reasoning_effort=reasoning_effort,
            verbosity=verbosity,
            task_iteration=config.current_iteration,
            llm_model=model.value,
            token_count=0,
            price=0,
            input_messages=[{
                "role": m.message_type.value,
                "content": llm_interface.sanitize_prompt(m.text)
            } for m in llm_messages]
        )
        
        try:
            max_tokens = MODEL_TO_MAX_TOKENS.get(model)
            llm_resp = llm_interface.chat(
                messages=llm_messages,
                model=model,
                output_schema=PROMPTS_DIR / output_schema if output_schema else None,
                code_response=code_response,
                debug=False
            )
            
            resp_json = llm_resp.__dict__.copy()
            resp_json.pop("text", None)
            resp_json.pop("parsed_json", None)
            
            LlmRequest.objects.filter(id=llm_request.id).update(
                response=llm_resp.text,
                resp_json=resp_json,
                token_count=llm_resp.token_count,
                price=llm_resp.price_total
            )
            
            break
        except Exception as e:
            config.log(e)
            
            LlmRequest.objects.filter(id=llm_request.id).update(
                response=traceback.format_exc(),
                token_count=0,
                price=0
            )
            
            if i == 1:
                raise e
            else:
                model = MODEL_BACKUPS[model]
    
    return llm_resp


def deploy_cloudformation_stacks(
        config: SelfDriverConfig,
        aws_env: AwsEnv,
        docker_env: dict,
        ecr_arn: str
):
    cf_client = boto3.client("cloudformation", region_name=aws_env.get_aws_region())
    
    self_driving_task = config.self_driving_task
    sandbox_path = Path(config.sandbox_root_dir)
    
    cfn_file = get_cloudformation_file(config)
    stack_name = self_driving_task.get_cloudformation_stack_name(aws_env)
    config.log(f"\n\n\n\n======== Begining cloudformation deploy for {stack_name} ")
    
    start_time = time.time()
    try:
        prepare_stack_for_update(
            config,
            stack_name,
            cf_client
        )
        
        try:
            if get_stack(stack_name, cf_client):
                assert_cloudformation_stack_valid(
                    stack_name,
                    cf_client
                )
        except Exception as e:
            logging.exception(e)
            config.log(traceback.format_exc())
            raise AgentBlocked(f"cloudformation stack {stack_name} in {aws_env.get_aws_region()} is wedged and cannot be autonomously fixed.  JJ or a Human needs to clean up manually")
        
        cloudformation_params = get_stack_parameters(
            config,
            aws_env,
            docker_env,
            cfn_file,
            ecr_arn
        )
        
        config.log(f"creating cloudformatin stack with params:  {json.dumps(cloudformation_params, indent=4)}")
        
        validate_parameters(
            config,
            cloudformation_params
        )
        
        push_cloudformation(
            config,
            stack_name,
            aws_env,
            cfn_file,
            cloudformation_params
        )
        
        config.log(f"======== COMPLETED cloudformation deploy for {stack_name}\n\n\n\n")
    except BadPlan as bpe:
        logging.exception(bpe)
        config.log(traceback.format_exc())
        raise bpe
    
    except AgentBlocked as abe:
        logging.exception(abe)
        config.log(traceback.format_exc())
        raise abe
    
    except CloudFormationException as cfe:
        config.log(f"Error deploying CloudFormation stack {stack_name}: {cfe}\n")
        config.log(traceback.format_exc())
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
            config.log("CloudFormation failure events:\n")
            config.log("\n".join(failed_events) + "\n")
            
            # Also describe the top 3 failed resources in more detail
            failed_logical_ids = [e["LogicalResourceId"] for e in recent_events if "FAILED" in e["ResourceStatus"]]
            config.log("\nDetailed resource descriptions for top failures:\n")
            for logical_id in failed_logical_ids[:3]:
                try:
                    resource_details = cf_client.describe_stack_resource(
                        StackName=stack_name,
                        LogicalResourceId=logical_id
                    )
                    config.log(json.dumps(resource_details, indent=2, default=str) + "\n")
                except Exception as ex:
                    config.log(f"Failed to describe resource {logical_id}: {ex}\n")
        except Exception as event_ex:
            config.log(f"Failed to fetch stack events: {event_ex}\n")
        
        from datetime import datetime, timedelta
        config.log("\nRecent CloudTrail events in this region (last 15 min):\n")
        try:
            ct_client = boto3.client("cloudtrail", region_name=aws_env.get_aws_region())
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
                    
                    config.log(f'\n\n\nCloudTrail Event: {ct_event.get("EventName")} at {ct_event.get("EventTime")}\n')
                    config.log(json.dumps(cloudtrain_event_data, indent=4))
        
        except Exception as ct_ex:
            config.log(f"Failed to fetch CloudTrail events: {ct_ex}\n")
        
        raise cfe


def validate_parameters(
        config: SelfDriverConfig,
        cloudformation_params
):
    pass


def get_stack_parameters(
        config: SelfDriverConfig,
        aws_env: AwsEnv,
        docker_env: dict,
        cfn_file: Path,
        ecr_arn: str
):
    """
    Build the CloudFormation Parameters array for this stack.
    - Pulls required parameters from the template
    - Injects standard params (StackIdentifier, ECRRepositoryArn, AWS_ACCOUNT_ID)
    - Resolves the RDS Secret ARN from the provided envvar->ARN pairs and planner output
    """
    self_driving_task = config.self_driving_task
    secrets_key = config.business.get_secrets_root_key(aws_env)
    
    required_parameters, parameters_metadata = extract_cloudformation_params(
        cfn_file
    )
    
    if "DBName" in required_parameters:
        raise BadPlan("infrastructure.yaml has a parameter named 'DBName'. **NEVER** add a parameter to infrastructure.yaml named 'DBName'.  Delete this parameter.  DB credentials are stored the secrets value fetched from the RdsSecretArn parameter", config.current_iteration.planning_json)
    
    if "DBPassword" in required_parameters:
        raise BadPlan("infrastructure.yaml has a parameter named 'DBPassword'. **NEVER** add a parameter to infrastructure.yaml named 'DBPassword'.  Delete this parameter.  DB credentials are stored the secrets value fetched from the RdsSecretArn parameter", config.current_iteration.planning_json)
    
    role_name = credential_manager.get_aws_role_name(config, aws_env)
    role_arn = aws_utils.ensure_iam_role_exists_and_get_arn(role_name)
    
    known_params = {
        "StackIdentifier": self_driving_task.get_cloudformation_key_prefix(aws_env),
        "ClientIpForRemoteAccess": common.get_ip_address(),
        "TaskRoleArn": role_arn,
        "ECRRepositoryArn": ecr_arn,
        "AWS_ACCOUNT_ID": settings.AWS_ACCOUNT_ID,
        
        # Intentionally DO NOT include legacy username/password params; use ARN pattern instead
        **get_admin_credentials(
            aws_env,
            secrets_key
        )
    }
    
    arn_param_bindings = []  # list of (param_name, envvar_name, arn)
    planning_required_creds = config.business.required_credentials or {}
    for svc_name, svc_spec in planning_required_creds.items():
        cfn_param = svc_spec.get("secret_arn_cfn_parameter")
        envvar_name = svc_spec.get("secret_arn_env_var")
        arn_value = docker_env.get(envvar_name)
        if cfn_param and arn_value:
            arn_param_bindings.append((cfn_param, envvar_name, arn_value))
    
    # Inject known ARNs for any required CFN params
    for param_name, envvar_name, arn in arn_param_bindings:
        if param_name in required_parameters and arn:
            known_params[param_name] = arn
    
    # Optionally pull additional params from a shared secret JSON at `secrets_key`
    aws_secrets_client = boto3.client("secretsmanager", region_name=aws_env.get_aws_region())
    try:
        response = aws_secrets_client.get_secret_value(SecretId=secrets_key)
        secret_params = json.loads(response.get("SecretString", "{}") or "{}")
        if isinstance(secret_params, dict):
            known_params = {**known_params, **secret_params}
    except aws_secrets_client.exceptions.ResourceNotFoundException:
        ...
    
    # Compute missing required params (ignoring those with defaults or marked optional)
    missing = set()
    for param in required_parameters:
        param_meta = parameters_metadata.get(param, {})
        desc = str(param_meta.get("Description", "")).lower()
        has_default = "Default" in param_meta
        is_optional = "(optional)" in desc or has_default
        if param not in known_params and not is_optional:
            missing.add(param)
    
    # Provide targeted hints for any missing secret-ARN CFN parameters
    missing_secret_params = [
        {"parameter": p, "expected_env_var": envvar_name, "resolved_arn": arn}
        for (p, envvar_name, arn) in arn_param_bindings
        if (p in required_parameters) and (p not in known_params)
    ]
    
    if missing_secret_params:
        raise BadPlan(json.dumps({
            "desc": "Missing required secret ARN CloudFormation parameter(s).",
            "file": cfn_file.name,
            "missing_secret_params": missing_secret_params,
            "available_env_vars": docker_env,
            "message": "Ensure credential_manager returned ARNs for these secrets and that they were passed into get_stack_parameters(envvar_secretarn_list=...)"
        }, indent=4), config.current_iteration.planning_json)
    
    if missing:
        raise BadPlan(json.dumps({
            "desc": "infrastructure.yaml specifies the following parameters, but agent unable to supply them.  Either the parameters are invalid (likely) or the agent needs to be modified (not likely)",
            "missing_parameters": sorted(missing),
            "file": cfn_file.name,
            "secret_hint": f"{secrets_key}/cloudformation"
        }, indent=4), config.current_iteration.planning_json)
    
    params = [
        {
            "ParameterKey": k,
            "ParameterValue": str(known_params.get(k, ""))
        }
        for k in required_parameters
    ]
    
    for p in params:
        key = p.get("ParameterKey")
        val = str(p.get("ParameterValue", "")).strip()
        # persist any trimming to avoid false pattern mismatches
        p["ParameterValue"] = val
        meta = parameters_metadata.get(key) if isinstance(parameters_metadata, dict) else None
        if isinstance(meta, dict) and "AllowedPattern" in meta and meta.get("AllowedPattern") is not None:
            pattern = str(meta.get("AllowedPattern") or "")
            try:
                if not re.fullmatch(pattern, val):
                    raise BadPlan(json.dumps({
                        "desc": f"Parameter '{key}' failed AllowedPattern validation",
                        "parameter": key,
                        "provided_value": val,
                        "allowed_pattern": pattern,
                        "hint": "Pass a value that matches the regex or relax/remove AllowedPattern in infrastructure.yaml"
                    }, indent=4), config.current_iteration.planning_json)
            except re.error as rex:
                raise BadPlan(json.dumps({
                    "desc": f"Invalid AllowedPattern regex for parameter '{key}' in infrastructure.yaml",
                    "allowed_pattern": pattern,
                    "regex_error": str(rex)
                }, indent=4), config.current_iteration.planning_json)
    
    return params


def get_admin_credentials(
        environment,
        secrets_key
):
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


def ecr_authenticate_for_dockerfile(config: SelfDriverConfig, dockerfile):
    try:
        with open(dockerfile) as f:
            pattern = r'FROM(?:\s+--platform=\$\w+)?\s+(\d+\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[^\s:]+:[^\s]+)'
            #   FROM 123456789012.dkr.ecr.region.amazonaws.com/repo:tag
            #   FROM --platform=$TARGETPLATFORM 123456789012.dkr.ecr.region.amazonaws.com/repo:tag
            for base_img in re.findall(pattern, f.read(), flags=re.IGNORECASE):
                ecr_login(config, base_img)
    except Exception as e:
        config.log(e)
        raise e


def ecr_login(config: SelfDriverConfig, ecr_repo_uri):
    region = parse_region_from_ecr_uri(ecr_repo_uri)
    subprocess.run(
        f"aws ecr get-login-password --region {region} | docker login --username AWS --password-stdin {ecr_repo_uri}",
        shell=True,
        check=True,
        stdout=config.log_f,
        stderr=subprocess.STDOUT
    )


def parse_region_from_ecr_uri(image_uri: str) -> str:
    try:
        parts = image_uri.split(".")
        if "ecr" in parts and len(parts) >= 4:
            return parts[3]  # region is always the 4th part
    except Exception:
        pass
    raise ValueError(f"Could not parse region from ECR URI: {image_uri}")


def get_file_structure_msg(root_dir: Path) -> list[LlmMessage]:
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
    
    return LlmMessage.user_from_data(
        "Existing Project File Structure (read-only reference for reuse planning)", {
            "existing_file_structure": structure
        }
    )


def delete_cloudformation_stack(config, aws_env: AwsEnv, block_while_waiting=True):
    if not AwsEnv.DEV.eq(aws_env):
        raise Exception(f"cannot delete a non DEV stack")
    
    stack_name = config.self_driving_task.cloudformation_stack_name
    if not stack_name:
        return
    
    cf_client = boto3.client("cloudformation", region_name=get_aws_region())
    existing = get_stack(stack_name, cf_client)
    if not existing:
        config.log(f"CloudFormation stack {stack_name} does not exist. Nothing to delete.")
        return
    
    empty_stack_buckets(config)
    
    config.log(f"Deleting CloudFormation stack {stack_name} in {get_aws_region()}")
    cf_client.delete_stack(StackName=stack_name)
    
    # Wait until the stack is deleted or reaches a terminal state
    if block_while_waiting:
        cloudformation_wait(config, cf_client, stack_name)
        config.log(f"Delete request finished for stack {stack_name}")


def empty_stack_buckets(config: SelfDriverConfig):
    stack_name = config.self_driving_task.cloudformation_stack_name
    
    cf_client = boto3.client("cloudformation", region_name=get_aws_region())
    existing = get_stack(stack_name, cf_client)
    if not existing:
        return
    
    resources = boto3.client(
        "cloudformation",
        region_name=get_aws_region()
    ).describe_stack_resources(
        StackName=stack_name
    )['StackResources']
    
    s3_buckets = [
        resource for resource in resources
        if resource['ResourceType'] == 'AWS::S3::Bucket' and resource['ResourceStatus'] != 'DELETE_COMPLETE'
    ]
    
    if not s3_buckets:
        return
    
    config.log(f"Found {len(s3_buckets)} S3 bucket(s) in stack {stack_name}, emptying before deletion...")
    s3_client = boto3.client("s3", region_name=get_aws_region())
    
    for bucket_resource in s3_buckets:
        bucket_name = bucket_resource['PhysicalResourceId']
        try:
            # Check if bucket exists before trying to empty it
            s3_client.head_bucket(Bucket=bucket_name)
            
            # Empty the bucket by deleting all objects and versions
            config.log(f"Emptying S3 bucket: {bucket_name}")
            
            # Delete all object versions and delete markers
            paginator = s3_client.get_paginator('list_object_versions')
            for page in paginator.paginate(Bucket=bucket_name):
                objects_to_delete = []
                
                # Add all versions
                for version in page.get('Versions', []):
                    objects_to_delete.append({
                        'Key': version['Key'],
                        'VersionId': version['VersionId']
                    })
                
                # Add all delete markers
                for marker in page.get('DeleteMarkers', []):
                    objects_to_delete.append({
                        'Key': marker['Key'],
                        'VersionId': marker['VersionId']
                    })
                
                # Delete objects in batches
                if objects_to_delete:
                    s3_client.delete_objects(
                        Bucket=bucket_name,
                        Delete={'Objects': objects_to_delete}
                    )
            
            config.log(f"Successfully emptied S3 bucket: {bucket_name}")
        
        except s3_client.exceptions.NoSuchBucket:
            config.log(f"S3 bucket {bucket_name} no longer exists, skipping...")


# Helper to extract CloudWatch stack logs for a time window
def extract_cloudwatch_stack_logs_for_window(
        config: SelfDriverConfig,
        start_time: int,
        end_time: int,
        max_groups: int = 50
) -> str:
    """
    Given a CloudFormation stack (resolved from the current task/env), collect CloudWatch Logs
    from relevant log groups (Lambda functions and explicit LogGroup resources) within the
    provided [start_time, end_time] window (epoch seconds). Returns a concatenated text block.
    """
    try:
        cf = boto3.client("cloudformation", region_name=get_aws_region())
        logs = boto3.client("logs", region_name=get_aws_region())
    except Exception as e:
        config.log(f"Unable to create AWS clients: {e}")
        return ""
    
    # Determine stack name
    try:
        stack_name = config.self_driving_task.cloudformation_stack_name
        if not stack_name:
            return ""
    except Exception as e:
        config.log(f"Could not resolve stack name: {e}")
        return ""
    
    # Collect candidate log group names from stack resources
    log_group_names = []
    try:
        paginator = cf.get_paginator("list_stack_resources")
        for page in paginator.paginate(StackName=stack_name):
            for r in page.get("StackResourceSummaries", []):
                rtype = r.get("ResourceType", "")
                phys = r.get("PhysicalResourceId", "")
                # Explicit log groups
                if rtype == "AWS::Logs::LogGroup" and phys:
                    # PhysicalResourceId for LogGroup can be the full name or ARN; normalize to name
                    name = phys
                    if ":log-group:" in name:
                        # ARN format: arn:aws:logs:region:acct:log-group:NAME:*
                        name = name.split(":log-group:", 1)[-1].split(":")[0]
                    if name:
                        log_group_names.append(name)
                # Lambda functions -> /aws/lambda/<function name>
                elif rtype == "AWS::Lambda::Function" and phys:
                    log_group_names.append(f"/aws/lambda/{phys}")
    except Exception as e:
        config.log(f"Failed to enumerate stack resources for {stack_name}: {e}")
    
    # Fallback: include lambda groups whose names contain the stack name
    if len(log_group_names) == 0:
        try:
            next_token = None
            while True:
                kwargs = {"logGroupNamePrefix": "/aws/lambda/"}
                if next_token:
                    kwargs["nextToken"] = next_token
                resp = logs.describe_log_groups(**kwargs)
                for lg in resp.get("logGroups", []):
                    name = lg.get("logGroupName")
                    if name and stack_name in name:
                        log_group_names.append(name)
                        if len(log_group_names) >= max_groups:
                            break
                if len(log_group_names) >= max_groups or not resp.get("nextToken"):
                    break
                next_token = resp.get("nextToken")
        except Exception as e:
            config.log(f"Fallback discovery failed: {e}")
    
    # Deduplicate and clip to max_groups
    log_group_names = list(dict.fromkeys([n for n in log_group_names if isinstance(n, str) and n.strip()]))[:max_groups]
    if not log_group_names:
        return ""
    
    # Logs Insights query over the time window - no RequestId filter, just the window
    query_str = (
        "fields @timestamp, @log, @message "
        "| sort @timestamp asc "
        "| limit 2000"
    )
    
    combined = []
    batch_size = 10
    for i in range(0, len(log_group_names), batch_size):
        batch = log_group_names[i:i + batch_size]
        try:
            q = logs.start_query(
                logGroupNames=batch,
                startTime=int(start_time),
                endTime=int(end_time),
                queryString=query_str
            )
            query_id = q["queryId"]
        except Exception as e:
            config.log(f"start_query failed for batch {batch}: {e}")
            continue
        
        status = "Running"
        for _ in range(60):
            time.sleep(1)
            resp = logs.get_query_results(queryId=query_id)
            status = resp.get("status")
            if status in ("Complete", "Failed", "Cancelled", "Timeout"):
                results = resp.get("results", [])
                for item in results:
                    fields = {f.get("field"): f.get("value") for f in item}
                    ts = fields.get("@timestamp", "")
                    lg = fields.get("@log", "")
                    msg = fields.get("@message", "")
                    combined.append(f"{ts}  {lg}\n{msg}")
                break
        
        if status != "Complete":
            config.log(f"Logs Insights window query did not complete (status={status}) for batch {batch}")
    
    return "\n\n".join(combined)
