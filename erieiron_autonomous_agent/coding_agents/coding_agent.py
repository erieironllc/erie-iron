import json
import logging
import os
import re
import shutil
import subprocess
import textwrap
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from django.db import transaction
from django.db.models import Func
from erieiron_public import agent_tools

import settings
from erieiron_autonomous_agent.coding_agents import credential_manager
from erieiron_autonomous_agent.coding_agents.code_writer import write_code
from erieiron_autonomous_agent.coding_agents.frontier_session import CommandPlan, CommandSpec, CommandResult
from erieiron_autonomous_agent.coding_agents.coding_agent_config import SdaPhase, LAMBDA_PACKAGES_BUCKET, ERIEIRON_PUBLIC_COMMON_VERSION, SdaInitialAction, CodingAgentConfig, MIN_PODMAN_STORAGE_FREE_GB, ENVVAR_TO_STACK_OUTPUT
from erieiron_autonomous_agent.coding_agents.self_driving_coder_exceptions import AgentBlocked, NeedPlan, RetryableException, BadPlan, GoalAchieved, CodeReviewException, ExecutionException, FailingTestException, DatabaseMigrationException
from erieiron_autonomous_agent.enums import TaskStatus
from erieiron_autonomous_agent.models import Business, CodeVersion, SelfDrivingTaskIteration, Task, SelfDrivingTask, CodeFile, AgentLesson, AgentTombstone, InfrastructureStack, Initiative, TaskExecution
from erieiron_autonomous_agent.system_agent_llm_interface import llm_chat, get_sys_prompt
from erieiron_common import common, aws_utils, ses_manager
from erieiron_common.aws_utils import sanitize_aws_name, package_lambda
from erieiron_common.chat_engine.language_utils import get_text_embedding
from erieiron_common.enums import LlmModel, PubSubMessageType, TaskType, TaskExecutionSchedule, EnvironmentType, LlmReasoningEffort, CredentialService, ContainerPlatform, InfrastructureStackType, LlmCreativity, LlmVerbosity, CredentialServiceProvisioning, CredentialsSpace, IterationMode, BuildStep, TaskImplementationSourceKind
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_common.message_queue.pubsub_manager import PubSubManager
from erieiron_common.opentofu_helpers import OpenTofuException, OpenTofuCommandException, MissingStackPerms
from erieiron_common.stack_manager import StackManager


TASK_EVALUATOR_OUTPUT_SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {
            "score": {"type": "number", "minimum": 0, "maximum": 1},
            "metadata": {"type": "object"},
        },
        "required": ["score"],
        "additionalProperties": False,
    }
)


@dataclass(frozen=True)
class RepoBackedTaskRunResult:
    output: dict[str, Any]
    model_metadata: dict[str, Any] | None = None
    evaluation_score: float | None = None
    evaluation_metadata: dict[str, Any] | None = None


def execute(
        task_id: str,
        one_off_action: SdaInitialAction = None,
        restart=False
):
    try:
        self_driving_task = bootstrap_selfdriving_agent(task_id, restart)
        
        if one_off_action:
            with CodingAgentConfig(self_driving_task, one_off_action) as config:
                execute_one_off_action(config, one_off_action)
            return
    except AgentBlocked as agent_blocked:
        logging.exception(agent_blocked)
        handle_agent_blocked(task_id, agent_blocked)
        logging.info(f"Stopping - Agent Blocked")
        return
    
    for i in range(20):
        self_driving_task.get_git().pull()
        
        with CodingAgentConfig(self_driving_task) as config:
            try:
                if config.budget and config.self_driving_task.get_cost() > config.budget:
                    logging.info(f"Stopping - hit the max budget ${config.budget :.2f}")
                    break
                
                if i == 0:
                    bootstrap_first_cycle(
                        config,
                        self_driving_task
                    )
                else:
                    config.set_iteration(self_driving_task.iterate())
                    
                    if config.iteration_to_modify and config.iteration_to_modify != config.previous_iteration and i > 0:
                        config.iteration_to_modify.write_to_disk()
                    
                    plan_code_changes(config)
                    write_code(config)
                
                build_deploy_exec_iteration(config)
                
                evaluate_iteration(
                    config
                )
            
            except NeedPlan as npe:
                logging.info(f'NeedPlan - {npe}')
            except RetryableException as retryable_execution_exception:
                config.log(retryable_execution_exception)
                with transaction.atomic():
                    SelfDrivingTaskIteration.objects.filter(id=config.current_iteration.id).update(
                        log_content_execution=textwrap.dedent(f"""
                            Execution Failed with 
                            {retryable_execution_exception}
                            {traceback.format_exc()}
                                    
                            planning data:
                            We should just try again - should be fixed next time around

                            full logs:
                            {config.get_log_content()}
                        """))
                config.current_iteration.refresh_from_db(fields=["log_content_execution"])
            except BadPlan as bad_plan_exception:
                config.log(bad_plan_exception)
                with transaction.atomic():
                    SelfDrivingTaskIteration.objects.filter(id=config.current_iteration.id).update(
                        log_content_execution=textwrap.dedent(f"""
                            Planning agent produced a bad plan:
                            {bad_plan_exception}
                            {traceback.format_exc()}

                            planning data:
                            {json.dumps(bad_plan_exception.plan_data, indent=4)}

                            full logs:
                            {config.get_log_content()}
                        """))
                config.current_iteration.refresh_from_db(fields=["log_content_execution"])
            except AgentBlocked as agent_blocked:
                logging.exception(agent_blocked)
                try:
                    print("Current Stack Outputs")
                    for k, v in config.stack_manager.get_outputs().items():
                        print(f"- `{k}` = `{v}`")
                except:
                    ...
                handle_agent_blocked(task_id, agent_blocked)
                config.log(common.json_format_pretty(agent_blocked.blocked_data))
                logging.info(f"Stopping - Agent Blocked")
                break
            except GoalAchieved as goal_achieved:
                handle_goal_achieved(config)
                logging.info(f"Stopping - Goal Achieved")
                break
            
            except Exception as e:
                logging.exception(e)
                config.log(e)
                logging.info(f"Stopping - Unhandled Exception")
                raise e
            finally:
                config.cleanup_iteration()


def bootstrap_first_cycle(config: CodingAgentConfig, self_driving_task: SelfDrivingTask):
    most_recent_iteration = self_driving_task.get_most_recent_iteration()
    
    if most_recent_iteration:
        # we've re-started an self driving task - re-execute on the first time around
        config.set_iteration(most_recent_iteration)
        
        CodeVersion.objects.filter(task_iteration_id=config.current_iteration.id).delete()
        
        SelfDrivingTaskIteration.objects.filter(id=config.current_iteration.id).update(
            slowest_cloudformation_resources=None,
            log_content_execution=None,
            log_content_coding=None,
            log_content_evaluation=None,
            log_content_init=None,
            evaluation_json=None
        )
        config.current_iteration.refresh_from_db()
    else:
        # we've started a new self driving task and this is the first iteration
        config.set_iteration(self_driving_task.iterate())


def execute_one_off_action(config: CodingAgentConfig, one_off_action: SdaInitialAction):
    config.init_log()
    
    print(textwrap.dedent(f"""
            
            
            Running {one_off_action} for
            {config.task.id}
            {config.task.description}
            
            
            """))
    
    config.set_iteration(
        config
        .self_driving_task
        .selfdrivingtaskiteration_set
        # .filter(planning_json__isnull=False)
        .order_by("-timestamp")
        .first()
    )
    
    if SdaInitialAction.STACK_PUSH.eq(one_off_action):
        container_image_tag = config.aws_interface.get_latest_ecr_image_tag(
            config.ecr_repo_name
        )
        
        deploy_stack(
            config=config,
            container_image_tag=container_image_tag
        )
        evaluate_iteration(config)
        return
    
    if one_off_action in [SdaInitialAction.PLAN, SdaInitialAction.CODE]:
        config.current_iteration.log_content_coding = None
        config.current_iteration.save()
    
    if not SdaInitialAction.EVAL.eq(one_off_action):
        config.current_iteration.log_content_coding = None
        config.current_iteration.log_content_execution = None
        config.current_iteration.log_content_evaluation = None
        config.current_iteration.iac_logs = None
        config.current_iteration.evaluation_json = None
        config.current_iteration.save()
    
    if SdaInitialAction.CODE.eq(one_off_action):
        write_code(config)
    elif SdaInitialAction.PLAN.eq(one_off_action):
        # config.iteration_to_modify.strategic_unblocking_json = get_strategic_unblocking_data(config)
        # config.iteration_to_modify.save()
        extract_exceptions(config, config.iteration_to_modify)
        
        plan_code_changes(config)
        write_code(config)
    
    if not SdaInitialAction.EVAL.eq(one_off_action):
        try:
            build_deploy_exec_iteration(
                config
            )
        except GoalAchieved:
            handle_goal_achieved(config)
            return
    
    evaluate_iteration(config)


def handle_agent_blocked(task_id, agent_blocked):
    if not task_id:
        return
    
    # with transaction.atomic():
    #     Task.objects.filter(id=task_id).update(
    #         status=TaskStatus.BLOCKED
    #     )
    
    PubSubManager.publish(
        PubSubMessageType.TASK_BLOCKED,
        payload={
            "blocked_data": json.dumps(agent_blocked.blocked_data),
            "task_id": task_id
        }
    )


def handle_goal_achieved(config):
    if TaskType.PRODUCTION_DEPLOYMENT.eq(config.task_type):
        Task.objects.filter(id=config.task.id).update(
            status=TaskStatus.COMPLETE
        )
        return

    if _uses_repo_backed_task_runtime(config.task):
        Task.objects.filter(id=config.task.id).update(
            status=TaskStatus.COMPLETE
        )
        config.git.cleanup()
        return
    
    if TaskType.CODING_ML.eq(config.task_type):
        from erieiron_autonomous_agent.coding_agents.ml_packager import package_ml_artifacts
        package_ml_artifacts(config)
    
    try:
        common.quietly_delete(config.sandbox_root_dir / "artifacts")
        config.git.add_commit_push(
            f"task {config.task.id}: {config.task.description}"
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
    finally:
        config.git.cleanup()


def _uses_repo_backed_task_runtime(task: Task) -> bool:
    return (
        TaskType.TASK_EXECUTION.eq(task.task_type)
        and task.get_active_implementation_version() is not None
    )


def _build_task_output_schema(task: Task) -> str | None:
    output_fields = [field for field in common.ensure_list(task.output_fields) if common.default_str(field).strip()]
    if not output_fields:
        return None
    return json.dumps(
        {
            "type": "object",
            "properties": {field: {} for field in output_fields},
            "required": output_fields,
            "additionalProperties": True,
        }
    )


def _best_effort_ingest_task_runtime_env(config: CodingAgentConfig) -> dict[str, str]:
    try:
        ingest_tofu_ouputs(config)
    except Exception as exc:
        logging.info("unable to ingest stack outputs for repo-backed task %s: %s", config.task.id, exc)
        config.log(f"Unable to ingest stack outputs: {exc}")
    env = os.environ.copy()
    env.update(config.runtime_env)
    return env


def _update_task_execution_revision(task_execution, revision: str | None):
    if not revision:
        return
    provenance = {
        **(task_execution.implementation_provenance or {}),
        "application_repo_revision": revision,
        "instance_config_revision": revision,
    }
    TaskExecution.objects.filter(id=task_execution.id).update(
        implementation_provenance=provenance
    )
    task_execution.implementation_provenance = provenance


def _coerce_llm_task_output(response_text: str) -> dict[str, Any]:
    stripped = common.default_str(response_text).strip()
    if not stripped:
        return {}
    try:
        parsed = json.loads(stripped)
    except Exception:
        return {"result": stripped}
    if isinstance(parsed, dict):
        return parsed
    return {"result": parsed}


def _get_task_execution_io_paths(config: CodingAgentConfig) -> tuple[Path, Path]:
    task_io_dir = Path(config.sandbox_root_dir) / "task_io"
    task_io_dir.mkdir(parents=True, exist_ok=True)
    input_file = task_io_dir / f"{config.task.id}-input.json"
    output_file = task_io_dir / f"{config.task.id}-output.json"
    return input_file, output_file


def _write_task_execution_input(task_execution, input_file: Path):
    common.write_json(input_file, task_execution.input or {})


def _resolve_management_command_name(relative_path: Path) -> str | None:
    parts = relative_path.parts
    if len(parts) < 3:
        return None
    for idx, part in enumerate(parts[:-2]):
        if part == "management" and parts[idx + 1] == "commands":
            return relative_path.stem
    return None


def _run_repo_backed_code_task(
        config: CodingAgentConfig,
        task_execution,
        version,
        runtime_env: dict[str, str],
        input_file: Path,
        output_file: Path,
) -> dict[str, Any]:
    source_path = version.get_source_file_path(config.sandbox_root_dir)
    relative_source_path = source_path.relative_to(config.sandbox_root_dir)
    command_name = _resolve_management_command_name(relative_source_path)
    if command_name:
        python_cmd = [
            "manage.py",
            command_name,
            "--input_file",
            input_file,
            "--output_file",
            output_file,
        ]
    elif source_path.suffix == ".py":
        python_cmd = [
            relative_source_path,
            "--input_file",
            input_file,
            "--output_file",
            output_file,
        ]
    else:
        raise AgentBlocked(
            {
                "description": f"Unsupported code task entrypoint {relative_source_path}",
                "task_id": config.task.id,
            }
        )
    config.log(
        f"Running repo-backed code implementation from {relative_source_path} @ {version.application_repo_ref}"
    )
    result = config.git.run_python(python_cmd, runtime_env)
    if result.stdout:
        config.log(result.stdout)
    if result.stderr:
        config.log(result.stderr)
    if not output_file.exists():
        raise ExecutionException(f"Task execution did not write {output_file.name}")
    return common.read_json(output_file, default={}) or {}


def _run_repo_backed_prompt_task(
        config: CodingAgentConfig,
        task_execution,
        version,
) -> tuple[dict[str, Any], dict[str, Any]]:
    prompt_text = version.get_prompt_text(config.sandbox_root_dir)
    output_schema = version.get_output_schema_file_path(config.sandbox_root_dir)
    if not output_schema:
        output_schema = _build_task_output_schema(config.task)
    model = LlmModel.valid_or(
        common.get(version.source_metadata, "model"),
        LlmModel.OPENAI_GPT_5_MINI,
    )
    reasoning_effort = LlmReasoningEffort.valid_or(
        common.get(version.source_metadata, "reasoning_effort"),
        LlmReasoningEffort.LOW,
    )
    verbosity = LlmVerbosity.valid_or(
        common.get(version.source_metadata, "verbosity"),
        LlmVerbosity.LOW,
    )
    creativity = LlmCreativity.valid_or(
        common.get(version.source_metadata, "creativity"),
        LlmCreativity.NONE,
    )
    response = llm_chat(
        f"Execute task {config.task.id}",
        [
            LlmMessage.sys(prompt_text),
            LlmMessage.user_from_data(
                "Task Execution Context",
                {
                    "task_id": config.task.id,
                    "task_description": config.task.description,
                    "completion_criteria": config.task.completion_criteria,
                    "input_fields": config.task.input_fields,
                    "output_fields": config.task.output_fields,
                    "input": task_execution.input or {},
                },
            ),
        ],
        tag_entity=config.current_iteration,
        model=model,
        output_schema=output_schema,
        reasoning_effort=reasoning_effort,
        verbosity=verbosity,
        creativity=creativity,
        code_response=bool(output_schema),
    )
    output_data = response.json() if output_schema else _coerce_llm_task_output(response.text)
    return output_data, {
        "llm_model": str(response.model),
        "prompt_file_path": version.get_prompt_source_label(),
        "reasoning_effort": reasoning_effort.value,
        "verbosity": verbosity.value,
        "creativity": creativity.value,
    }


def _run_repo_backed_evaluator(
        config: CodingAgentConfig,
        task_execution,
        version,
        runtime_env: dict[str, str],
        output_data: dict[str, Any],
) -> tuple[float | None, dict[str, Any] | None]:
    evaluator_spec = version.get_evaluator_spec()
    evaluator_config = evaluator_spec["config"]
    if not evaluator_config:
        return None, None
    evaluation_input = {
        "task_id": config.task.id,
        "task_description": config.task.description,
        "input": task_execution.input or {},
        "output": output_data or {},
        "implementation_provenance": task_execution.implementation_provenance or {},
    }
    if TaskImplementationSourceKind.CODE_FILE.eq(version.source_kind):
        evaluator_path = version.get_evaluator_file_path(config.sandbox_root_dir)
        input_file = Path(config.sandbox_root_dir) / "task_io" / f"{config.task.id}-evaluation-input.json"
        output_file = Path(config.sandbox_root_dir) / "task_io" / f"{config.task.id}-evaluation-output.json"
        common.write_json(input_file, evaluation_input)
        relative_evaluator_path = evaluator_path.relative_to(config.sandbox_root_dir)
        command_name = _resolve_management_command_name(relative_evaluator_path)
        if command_name:
            python_cmd = [
                "manage.py",
                command_name,
                "--input_file",
                input_file,
                "--output_file",
                output_file,
            ]
        else:
            python_cmd = [
                relative_evaluator_path,
                "--input_file",
                input_file,
                "--output_file",
                output_file,
            ]
        result = config.git.run_python(python_cmd, runtime_env)
        if result.stdout:
            config.log(result.stdout)
        if result.stderr:
            config.log(result.stderr)
        evaluator_output = common.read_json(output_file, default={}) or {}
    else:
        evaluator_path = version.get_evaluator_file_path(config.sandbox_root_dir)
        evaluator_output = llm_chat(
            f"Evaluate task {config.task.id}",
            [
                LlmMessage.sys(evaluator_path.read_text()),
                LlmMessage.user_from_data("Task Execution Result", evaluation_input),
            ],
            tag_entity=config.current_iteration,
            model=LlmModel.valid_or(
                common.get(evaluator_config, "model"),
                LlmModel.OPENAI_GPT_5_MINI,
            ),
            output_schema=version.get_evaluator_output_schema_file_path(config.sandbox_root_dir) or TASK_EVALUATOR_OUTPUT_SCHEMA,
            reasoning_effort=LlmReasoningEffort.valid_or(
                common.get(evaluator_config, "reasoning_effort"),
                LlmReasoningEffort.LOW,
            ),
            verbosity=LlmVerbosity.valid_or(
                common.get(evaluator_config, "verbosity"),
                LlmVerbosity.LOW,
            ),
            creativity=LlmCreativity.valid_or(
                common.get(evaluator_config, "creativity"),
                LlmCreativity.NONE,
            ),
            code_response=True,
        ).json()
    return evaluator_output.get("score"), evaluator_output.get("metadata")


def _set_repo_backed_iteration_result(
        config: CodingAgentConfig,
        task_execution,
        *,
        goal_achieved: bool,
        summary: str,
        blocked_data: dict[str, Any] | None = None,
):
    evaluation_json = {
        "goal_achieved": goal_achieved,
        "allow_goal_achieved": goal_achieved,
        "summary": summary,
        "task_execution_id": str(task_execution.id),
        "task_execution_status": task_execution.status,
        "evaluation_score": task_execution.evaluation_score,
    }
    if blocked_data:
        evaluation_json["blocked"] = blocked_data
    SelfDrivingTaskIteration.objects.filter(id=config.current_iteration.id).update(
        evaluation_json=evaluation_json
    )
    config.current_iteration.refresh_from_db(fields=["evaluation_json"])


def _run_repo_backed_task_execution(
        config: CodingAgentConfig,
        task_execution,
) -> RepoBackedTaskRunResult:
    version = task_execution.implementation_version or config.task.get_active_implementation_version()
    if not version:
        raise AgentBlocked(
            {
                "description": f"Task {config.task.id} is missing an active implementation version",
                "task_id": config.task.id,
            }
        )
    runtime_env = _best_effort_ingest_task_runtime_env(config)
    config.dump_env_to_envrc()
    revision = config.git.checkout_ref(version.application_repo_ref)
    _update_task_execution_revision(task_execution, revision)
    input_file, output_file = _get_task_execution_io_paths(config)
    _write_task_execution_input(task_execution, input_file)
    if TaskImplementationSourceKind.LLM_PROMPT.eq(version.source_kind):
        output_data, model_metadata = _run_repo_backed_prompt_task(
            config,
            task_execution,
            version,
        )
        common.write_json(output_file, output_data)
    else:
        output_data = _run_repo_backed_code_task(
            config,
            task_execution,
            version,
            runtime_env,
            input_file,
            output_file,
        )
        model_metadata = None
    evaluation_score, evaluation_metadata = _run_repo_backed_evaluator(
        config,
        task_execution,
        version,
        runtime_env,
        output_data,
    )
    return RepoBackedTaskRunResult(
        output=output_data,
        model_metadata=model_metadata,
        evaluation_score=evaluation_score,
        evaluation_metadata=evaluation_metadata,
    )


def on_reset_task_test(task_id):
    task = Task.objects.get(id=task_id)
    self_driving_task = task.selfdrivingtask
    config = CodingAgentConfig(self_driving_task)
    if config.task_type.eq(TaskType.PRODUCTION_DEPLOYMENT):
        config.log("Skipping test regeneration for production deployment task")
        return config
    
    write_tdd_test(config)
    
    return config


def write_tdd_test(config: CodingAgentConfig):
    plan_code_changes(config, write_tdd_test=True)
    write_code(config)


def get_guidance_msg(config: CodingAgentConfig):
    return config.guidance


def plan_code_changes(config: CodingAgentConfig, write_tdd_test=False):
    config.set_phase(SdaPhase.PLANNING)
    
    if write_tdd_test:
        if TaskType.INITIATIVE_VERIFICATION.eq(config.task_type):
            planning_data = plan_initiative_tdd_code_changes(config)
        else:
            planning_data = plan_task_tdd_code_changes(config)
    else:
        planning_data = plan_iterative_code_changes(config)
        if "tdd_test_file" in planning_data:
            # planner made a mistake.  tdd_test_file only valid when writing a test.  just clean it up
            planning_data.pop("tdd_test_file", None)
    
    validate_plan(
        config,
        planning_data
    )
    
    merge_planner_required_credentials(
        config.business,
        planning_data
    )
    
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


def merge_planner_required_credentials(business: Business, planning_data: dict) -> None:
    """
    Merge any required_credentials discovered by the planner into the Business model.

    This allows the coding planner to dynamically add credential requirements (like COGNITO)
    when it discovers they are needed during the development iteration loop.
    """
    planner_credentials = planning_data.get("required_credentials")
    if not planner_credentials:
        return
    
    current_credentials = business.required_credentials or []
    merged_credentials = list(sorted(set(current_credentials + planner_credentials)))
    
    # Only update if there are actual changes
    if merged_credentials != current_credentials:
        logging.info(
            f"Merging planner-discovered credentials into Business {business.service_token}: "
            f"added {set(merged_credentials) - set(current_credentials)}"
        )
        Business.objects.filter(id=business.id).update(
            required_credentials=merged_credentials
        )
        business.refresh_from_db(fields=["required_credentials"])


def get_strategic_unblocking_data(config: CodingAgentConfig):
    return llm_chat(
        "Get Stragic Unblocking Data",
        [
            get_sys_prompt([
                "codeplanning--strategic_unblocker.md",
                "common--forbidden_actions_tofu.md"
            ]),
            get_architecture_docs(
                config.initiative
            ),
            config.business.get_existing_required_credentials_llmm(),
            get_budget_message(
                config
            ),
            build_opentofu_plan_context_messages(
                config
            ),
            get_tombstone_message(
                config
            ),
            get_previous_iteration_summaries_msg(
                config
            ),
            build_previous_iteration_context_messages(
                config
            ),
            get_dependencies_msg(
                config,
                for_planning=True
            ),
            get_docs_msg(
                config
            ),
            get_file_structure_msg(
                config.sandbox_root_dir
            ),
            get_guidance_msg(
                config
            ),
            get_lessons_msg(
                "Do not repeat these mistakes - before you respond, checklist each item to make sure you're not repeating it",
                config
            ),
            get_goal_msg(config, "Please think of ways to unblock the agent from reaching this goal ")
        ],
        output_schema="codeplanning--strategic_unblocker.md.schema.json",
        tag_entity=config.current_iteration
    ).json()


def validate_plan(config: CodingAgentConfig, planning_data):
    readonly_paths = config.self_driving_task.get_readonly_files()
    
    for f in planning_data.get("code_files", []):
        code_file_path = str(f.get("code_file_path"))
        
        if code_file_path.endswith("settings.py"):
            continue
        
        if "docker-compose" in code_file_path:
            raise BadPlan(f"All services must be defined in the existing Dockerfile. You may **never** use container orchestration tools like docker-compose", planning_data)
        
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


def bootstrap_selfdriving_agent(task_id, restart) -> SelfDrivingTask:
    task = Task.objects.get(id=task_id)
    
    if restart:
        task.restart()
    
    initiative_has_iterations = SelfDrivingTaskIteration.objects.filter(
        self_driving_task__task__initiative_id=task.initiative_id
    ).exists()
    
    Task.objects.filter(id=task.id).update(
        status=TaskStatus.IN_PROGRESS
    )
    task.refresh_from_db(fields=["status"])
    
    self_driving_task: SelfDrivingTask = task.create_self_driving_env()
    
    if not initiative_has_iterations:
        # no tests
        self_driving_task.initial_tests_pass = True
        self_driving_task.save()
    
    # Create symbolic link to sandbox_root_dir for debugging
    business = task.initiative.business
    initiative = task.initiative
    debug_link_dir = Path.home() / "src" / "erieiron_debug" / business.service_token / str(initiative.id)
    debug_link_dir.parent.mkdir(parents=True, exist_ok=True)
    
    # Remove existing link if it exists
    if debug_link_dir.exists() or debug_link_dir.is_symlink():
        debug_link_dir.unlink()
    
    # Create symbolic link to sandbox
    sandbox_path = Path(self_driving_task.sandbox_path)
    debug_link_dir.symlink_to(sandbox_path)
    
    print(textwrap.dedent(f"""
    SYMLINK TO SOURCE
    
    {debug_link_dir}
    
    """))
    
    with CodingAgentConfig(self_driving_task) as config:
        config.set_phase(SdaPhase.INIT)
        
        config.aws_interface.configure_nat_gateway()
        
        self_driving_task.get_git().pull()
        config.iterate_if_necessary()
        
        if not config.business.codefile_set.exists():
            config.git.mk_venv()
        
        if (
                TaskType.CODING_APPLICATION.eq(task.task_type)
                and
                common.invalid_file(config.sandbox_root_dir, self_driving_task.test_file_path)
        ):
            write_tdd_test(config)
        
        first_iteration = self_driving_task.selfdrivingtaskiteration_set.first()
        config.business.snapshot_code(first_iteration)
        if first_iteration != config.current_iteration:
            config.business.snapshot_code(config.current_iteration)
    
    return self_driving_task


def ensure_lb_alias_record(config: CodingAgentConfig):
    aws_interface = config.aws_interface
    
    if EnvironmentType.PRODUCTION.eq(config.env_type):
        domain_name = config.business.domain
    else:
        domain_name = config.initiative.domain
    
    # Post-deployment validation to ensure ECS tasks are accessible via ALB
    app_stack = config.stack
    app_outputs = config.stack_manager.get_outputs()
    security_group_id = app_stack.stack_vars["SecurityGroupId"]
    security_group_managed_externally = app_stack.stack_vars.get("SecurityGroupManagedExternally", False)
    vpc_cidr = app_stack.stack_vars["VpcCidr"]
    target_group_arn = app_outputs["TargetGroupArn"]
    cluster_name = app_outputs["EcsClusterName"]
    service_name = f"{app_stack.stack_namespace_token}-service"
    
    config.log("Starting post-deployment validation...")
    
    # Validate security group rules allow ALB to reach ECS tasks
    if security_group_managed_externally:
        config.log(
            f"Skipping security group validation for {security_group_id} "
            f"(SecurityGroupManagedExternally=true)"
        )
    else:
        # aws_interface.validate_security_group_rules(
        #     security_group_id,
        #     vpc_cidr
        # )
        config.log("Security group validation passed")
    
    # Wait for ECS service to be stable
    aws_interface.wait_for_ecs_service_stable(
        cluster_name,
        service_name,
        timeout_minutes=10
    )
    config.log("ECS service stability validation passed")
    
    # Diagnose current target group health before waiting
    aws_interface.diagnose_target_group_health(target_group_arn)
    
    # Wait for targets to be healthy in target group
    aws_interface.wait_for_target_group_healthy(
        target_group_arn,
        timeout_minutes=10
    )
    config.log("Target group health validation passed")
    config.log("✅ All post-deployment validations passed successfully")
    
    hosted_zone_id = config.domain_manager.find_hosted_zone_id(config.business.domain)
    if not domain_name or not hosted_zone_id:
        raise Exception(f"missing domain ({domain_name}) or hosted zone id ({hosted_zone_id})")
    
    load_balancer_arn = common.first(config.stack_manager.get_arns("aws_lb"))
    if not load_balancer_arn:
        config.log(f"No Application Load Balancer found in stacks {config.get_stack_names()}; skipping DNS alias update")
        return
    
    elbv2_client = aws_interface.client("elbv2")
    try:
        lb_resp = elbv2_client.describe_load_balancers(LoadBalancerArns=[load_balancer_arn])
        load_balancers = lb_resp.get("LoadBalancers", []) or []
    except Exception as exc:
        logging.exception("Failed to describe load balancer %s", load_balancer_arn)
        raise AgentBlocked(f"unable to describe load balancer {load_balancer_arn}: {exc}")
    
    if not load_balancers:
        raise AgentBlocked(f"load balancer {load_balancer_arn} not found after description call")
    
    load_balancer = load_balancers[0]
    dns_name = load_balancer.get("DNSName")
    canonical_zone_id = load_balancer.get("CanonicalHostedZoneId")
    ip_address_type = (load_balancer.get("IpAddressType") or "").lower()
    
    if not dns_name or not canonical_zone_id:
        raise AgentBlocked(f"load balancer {load_balancer_arn} is missing DNS attributes needed for alias creation")
    
    dual_stack = ip_address_type == "dualstack"
    
    try:
        config.domain_manager.upsert_subdomain_alias(
            hosted_zone_id=hosted_zone_id,
            record_name=domain_name,
            target_dns_name=dns_name,
            target_hosted_zone_id=canonical_zone_id,
            comment=f"Auto-configured by Erie Iron for task {config.self_driving_task.task_id}",
            dual_stack=dual_stack
        )
        
        config.domain_manager.wait_for_dns_propagation(
            domain_name,
            dns_name
        )
    except Exception as exc:
        logging.exception("Failed to upsert Route53 alias for %s", domain_name)
        raise AgentBlocked(f"failed to configure Route53 alias for {domain_name}: {exc}")
    
    config.log(f"Ensured Route53 alias for {domain_name} targets {dns_name}")


def build_container(config: CodingAgentConfig) -> str:
    config.set_phase(SdaPhase.BUILD)
    
    container_file = config.sandbox_root_dir / "Dockerfile"
    
    ensure_container_storage_capacity(config)
    
    current_iteration = config.current_iteration
    self_driving_task = current_iteration.self_driving_task
    
    container_image_tag_parts = [
        self_driving_task.business.name,
        self_driving_task.id,
        current_iteration.version_number
    ]
    
    # force a new container image tag to make sure OpenTofu updates
    if TaskType.PRODUCTION_DEPLOYMENT.eq(config.task_type) or config.one_off_action:
        container_image_tag_parts.append(str(time.time())[-5:])
    
    container_image_tag = sanitize_aws_name(container_image_tag_parts, max_length=128)
    
    config.log(f"\n\n\n\n======== Begining PODMAN Build for tag {container_image_tag} ")
    
    config.log(f"Building container image for platform: {ContainerPlatform.FARGATE}")
    container_build_cmd = common.strings([
        "podman",
        "build",
        "--memory", "16g",
        "--memory-swap", "16g",
        "--shm-size", "2g",
        "--platform", ContainerPlatform.FARGATE,
        "--build-arg", f"ERIEIRON_PUBLIC_COMMON_SHA={ERIEIRON_PUBLIC_COMMON_VERSION}",
        "-t", container_image_tag,
        "-f", container_file,
        container_file.parent
    ])
    
    ecr_authenticate_for_base_image(
        config,
        container_file
    )
    print(textwrap.dedent(f"""
    DUDE

    building with
    {common.safe_join(container_build_cmd, ' ')}

    """))
    
    config.log(f"\n\nstarting podman build with the command:\n{' '.join(container_build_cmd)}\n\n")
    build_process = subprocess.Popen(
        container_build_cmd,
        stdout=config.log_f,
        stderr=subprocess.STDOUT,
        text=True,
        env=config.get_env_for_credentials_space(CredentialsSpace.TARGET_ACCOUNT)
    )
    
    while build_process.poll() is None:
        time.sleep(1)
    
    if build_process.returncode != 0:
        handle_podman_build_failure(config, build_process.returncode)
    
    SelfDrivingTaskIteration.objects.filter(id=config.current_iteration.id).update(
        docker_tag=container_image_tag
    )
    config.current_iteration.refresh_from_db(fields=["docker_tag"])
    
    if config.stack_manager.get_db_is_running():
        validate_web_container(config)


def ensure_container_storage_capacity(
        config: CodingAgentConfig,
        min_free_gb: float = MIN_PODMAN_STORAGE_FREE_GB
) -> None:
    storage_path = get_podman_storage_path()
    if not storage_path:
        config.log("Unable to determine podman storage path; continuing without disk space guard.")
        return
    
    free_gb = _get_free_space_gb(storage_path)
    if free_gb >= min_free_gb:
        return
    
    config.log(
        f"Detected low disk space for podman storage at {storage_path} ({free_gb:.2f} GiB free). Running aggressive prune."
    )
    
    exec_container_prune(aggressive=True)
    
    free_gb_after_prune = _get_free_space_gb(storage_path)
    if free_gb_after_prune >= min_free_gb:
        config.log(
            f"Podman storage now has {free_gb_after_prune:.2f} GiB free after prune."
        )
        return
    
    message = (
        f"Podman storage path {storage_path} still has only {free_gb_after_prune:.2f} GiB free after prune. "
        "Free disk space and rerun."
    )
    config.log(message)
    raise AgentBlocked(message)


def handle_podman_build_failure(config: CodingAgentConfig, return_code: int) -> None:
    storage_path = get_podman_storage_path()
    
    if storage_path:
        free_gb = _get_free_space_gb(storage_path)
        if free_gb < MIN_PODMAN_STORAGE_FREE_GB:
            message = (
                f"Podman build failed (return code {return_code}) because {storage_path} has only {free_gb:.2f} GiB free. "
                "Clear disk space for Podman and retry the iteration."
            )
            config.log(message)
            raise AgentBlocked(message)
    
    raise Exception(f"Podman build failed with return code: {return_code}")


def get_podman_storage_path() -> Optional[Path]:
    try:
        podman_info = subprocess.run(
            ["podman", "info", "--format", "json"],
            capture_output=True,
            text=True,
            check=True
        )
        store_info = common.get_dict(json.loads(podman_info.stdout).get("store") or {})
        graph_root = store_info.get("graphRoot") or store_info.get("GraphRoot")
        if graph_root:
            candidate = Path(graph_root)
            if candidate.exists():
                return candidate
    except Exception as e:
        logging.exception(e)
    
    candidates: list[Path] = []
    storage_env = os.environ.get("CONTAINERS_STORAGE")
    if storage_env:
        candidates.append(Path(storage_env))
    
    candidates.extend([
        Path.home() / ".local" / "share" / "containers" / "storage",
        Path("/var/lib/containers/storage"),
    ])
    
    for candidate in candidates:
        if candidate.exists():
            return candidate
    
    return None


def _get_free_space_gb(path: Path) -> float:
    try:
        usage = shutil.disk_usage(path)
    except FileNotFoundError as exc:
        logging.exception(exc)
        raise AgentBlocked(f"Podman storage path {path} is not accessible.")
    
    return usage.free / (1024 ** 3)


def exec_container_prune(aggressive: bool = False):
    try:
        prune_cmd = ["podman", "system", "prune", "-f"]
        if aggressive:
            prune_cmd.append("-a")
            prune_cmd.append("--volumes")
        
        subprocess.run(prune_cmd, check=True)
        
        if aggressive:
            subprocess.run(["podman", "image", "prune", "-a", "-f"], check=True)
            subprocess.run(["podman", "builder", "prune", "-a", "-f"], check=True)
    except Exception as e:
        logging.exception(e)
        raise AgentBlocked("unable to run podman prune - is podman running?")


def get_aws_region():
    return os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or settings.AWS_DEFAULT_REGION_NAME


def get_env_var_names(config: CodingAgentConfig) -> str:
    env = config.runtime_env
    return ", ".join(env.keys())


def build_env_flags(env):
    env_flags: list[str] = []
    for k in list(env.keys()):
        if k in ["PATH"]:
            continue
        
        val = env.get(k)
        if val:
            env_flags += ["-e", f"{k}={val}"]
    
    return env_flags


def build_lambdas(config: CodingAgentConfig, ) -> list[dict]:
    s3 = config.aws_interface.client("s3")
    
    lambda_datas = get_stack_lambdas(config)
    if not lambda_datas:
        return []
    
    # Pre-pull the ARM64 Lambda Python image before building dependencies
    subprocess.run(
        [
            "podman", "pull", "--platform", ContainerPlatform.LAMBDA,
            "public.ecr.aws/lambda/python:3.11"
        ],
        check=True,
        stdout=config.log_f,
        stderr=subprocess.STDOUT
    )
    
    for lambda_data in lambda_datas:
        zip_path = None
        try:
            lambda_data['s3_key_name'] = s3_key_name = aws_utils.sanitize_aws_name(common.safe_join([
                config.task.id,
                common.get_basename(lambda_data["code_file_path"]),
                config.current_iteration.version_number,
                time.time()
            ], "-"), 1000) + ".zip"
            
            zip_path = package_lambda(
                config.sandbox_root_dir,
                lambda_data["code_file_path"],
                lambda_data["dependencies"],
                s3_key_name
            )
            
            s3.upload_file(
                str(zip_path),
                LAMBDA_PACKAGES_BUCKET,
                s3_key_name
            )
        finally:
            common.quietly_delete(zip_path)
    
    return lambda_datas


def get_stack_lambdas(config) -> list[dict]:
    lambdas = []
    
    for resource_definition in config.stack_manager.get_resource_definitions("aws_lambda_function"):
        s3_key_ref = resource_definition.get('s3_key')
        resource_name = "todo"
        if s3_key_ref:
            raise BadPlan('s3_key is required', resource_definition)
        
        source_file = common.first(config.sandbox_root_dir.rglob(
            resource_definition['handler'].split(".")[0] + ".py"
        ))
        
        if not (s3_key_ref and source_file):
            continue
        
        s3_key_param = get_lambda_s3_key_param(
            resource_definition["arn"],
            s3_key_ref
        )
        
        dependencies = get_lambda_dependencies(
            config,
            source_file
        )
        
        lambdas.append({
            "lambda_name": resource_name,
            "dependencies": dependencies,
            "code_file_path": source_file,
            "s3_key_param": s3_key_param
        })
    
    return lambdas


def get_lambda_s3_key_param(resource_name, s3_key_ref):
    if isinstance(s3_key_ref, dict):
        if "Ref" in s3_key_ref:
            return s3_key_ref["Ref"]
        else:
            raise BadPlan(f"Bad Lambda {resource_name}: S3Key is not a Ref — got {s3_key_ref}")
    
    s3_key_ref = s3_key_ref.strip()
    parts = s3_key_ref.split(None, 1)
    if s3_key_ref.startswith("!Ref") and len(parts) == 2:
        return parts[1]
    else:
        raise BadPlan(f"Bad Lambda {resource_name}: S3Key is not a Ref — got {s3_key_ref}")


def get_lambda_dependencies(config, source_file: str) -> list:
    candidate_path = Path(source_file)
    candidate_path_abs = (config.sandbox_root_dir / candidate_path)
    
    if not candidate_path_abs.exists():
        raise BadPlan(f"Bad Lambda {source_file}: file not found — expected at {candidate_path_abs}")
    
    code_file_code = candidate_path_abs.read_text()
    match = re.search(r'^# LAMBDA_DEPENDENCIES: (\[.*?])', code_file_code, flags=re.MULTILINE)
    if match:
        try:
            dependencies = json.loads(match.group(1))
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid LAMBDA_DEPENDENCIES in {candidate_path}: {e}")
    else:
        dependencies = []
    
    dependencies = [d for d in dependencies if 'erieron-public-common' not in d]
    dependencies.append("erieiron-public-common @ git+https://github.com/erieironllc/erieiron-public-common.git")
    
    return dependencies


def get_stack_buckets(config) -> list[dict]:
    buckets = []
    
    for resource_def in config.stack_manager.get_resource_definitions("aws_s3_bucket"):
        bucket_name_expr = resource_def.get("bucket")
        
        if isinstance(bucket_name_expr, str):
            bucket_physical_name = bucket_name_expr
            bucket_name_param = None
        elif isinstance(bucket_name_expr, dict) and "Ref" in bucket_name_expr and isinstance(bucket_name_expr["Ref"], str):
            bucket_physical_name = None
            bucket_name_param = bucket_name_expr["Ref"]
        else:
            bucket_physical_name = None
            bucket_name_param = None
        
        buckets.append({
            "bucket_name": bucket_name_expr,
            "bucket_physical_name": bucket_physical_name,
            "bucket_name_param": bucket_name_param,
            "properties": "TODO",
            "metadata": "TODO"
        })
    
    return buckets


def get_infrastructure_yaml_data(config: CodingAgentConfig, cf_template: InfrastructureStackType) -> dict:
    return agent_tools.parse_cloudformation_yaml(
        get_infrastructure_yaml_code(config, cf_template)
    )


def get_infrastructure_yaml_codeversion(config: CodingAgentConfig, cf_template: InfrastructureStackType):
    return CodeFile.get(
        config.business,
        cf_template.value
    ).get_latest_version(
        config.self_driving_task
    )


def get_infrastructure_yaml_code(config, cf_template: InfrastructureStackType):
    infrastructure_code_version = get_infrastructure_yaml_codeversion(config, cf_template)
    
    return infrastructure_code_version.code if infrastructure_code_version else None


def push_to_ecr(config: CodingAgentConfig):
    container_image_tag = config.task.get_container_image_tag()
    
    region = config.env_type.get_aws_region()
    ecr_client = config.aws_interface.client("ecr")
    repo_name = config.ecr_repo_name
    
    full_image_uri = config.aws_interface.get_full_image_uri(
        repo_name,
        container_image_tag,
        region
    )
    
    ecr_login(
        config,
        full_image_uri,
        CredentialsSpace.TARGET_ACCOUNT
    )
    
    config.log(f"\n\n\n\n======== Begining ECR Push to {full_image_uri} ")
    
    # --- BEGIN CREDENTIAL DIAGNOSTICS ---
    try:
        config.log("=== AWS identity before podman push ===")
        subprocess.run(
            ["aws", "sts", "get-caller-identity"],
            check=True,
            stdout=config.log_f,
            stderr=subprocess.STDOUT,
            env=config.runtime_env  # ensure this matches the env used for podman login
        )
        
        config.log("=== Podman system info ===")
        subprocess.run(
            ["podman", "system", "info"],
            check=True,
            stdout=config.log_f,
            stderr=subprocess.STDOUT,
            env=config.runtime_env
        )
        
        config.log("=== Podman registry auth.json ===")
        auth_path = os.path.expanduser("~/.config/containers/auth.json")
        subprocess.run(
            ["cat", auth_path],
            check=True,
            stdout=config.log_f,
            stderr=subprocess.STDOUT,
            env=config.runtime_env
        )
    
    except Exception as diag_e:
        config.log(f"[WARN] Diagnostic error: {diag_e}")
    # --- END CREDENTIAL DIAGNOSTICS ---
    
    for i in range(3):
        try:
            try:
                ecr_client.describe_repositories(repositoryNames=[repo_name])
            except ecr_client.exceptions.RepositoryNotFoundException:
                ecr_client.create_repository(repositoryName=repo_name)
            
            subprocess.run(
                ["podman", "tag", container_image_tag, full_image_uri],
                check=True,
                stdout=config.log_f,
                stderr=subprocess.STDOUT,
                env=config.runtime_env
            )
            
            subprocess.run(
                ["podman", "push", full_image_uri],
                check=True,
                stdout=config.log_f,
                stderr=subprocess.STDOUT,
                env=config.runtime_env
            )
            break
        except Exception as e:
            logging.exception(e)
            if i < 2:
                logging.info(f"failed to push to ECR on attempt {i + 1}")
                time.sleep(5)
            else:
                raise AgentBlocked(f"task {config.task.id} is failing to push {container_image_tag} to ECR. {e}")
    
    config.log(f"======== COMPLETED ECR Push to {full_image_uri}\n\n\n\n")


def run_django_container_command(
        config: CodingAgentConfig,
        command_args: list[str]
) -> None:
    return run_generic_container_command(
        config,
        ["python", "manage.py"] + command_args
    )


def run_generic_container_command(
        config: CodingAgentConfig,
        command: list[str],
        working_dir: str = "/app"
) -> None:
    """
    Run arbitrary command in container (not limited to manage.py).

    Args:
        config: CodingAgentConfig
        command: Full command to execute (e.g., ["npm", "test"])
        working_dir: Working directory inside container
    """
    container_image_tag = config.task.get_container_image_tag()
    
    cmd = [
        "podman", "run", "--rm",
        "--memory", "4g",
        "--memory-swap", "10g",
        "--platform", ContainerPlatform.FARGATE,
        "-v", f"{config.sandbox_root_dir}:/app",
        "-w", working_dir,
        *build_env_flags(config.runtime_env),
        container_image_tag,
        *command
    ]
    
    config.log("\n" + "=" * 50 + "\n")
    config.log(f"RUNNING {' '.join(cmd)} in {config.sandbox_root_dir}\n")
    config.log("=" * 50 + "\n")
    
    cmd_no_envs = " ".join([
        "podman", "run", "--rm",
        "--memory", "4g",
        "--memory-swap", "10g",
        "--platform", ContainerPlatform.FARGATE,
        "-v", f"{config.sandbox_root_dir}:/app",
        "-w", working_dir,
        "--env-file", ".envrc",
        container_image_tag,
        *command
    ])
    
    print("=" * 50 + "\n")
    print(f'\n\n\n\nDUDE\n{cmd_no_envs}\nin {config.sandbox_root_dir}\n\n\n\n\n')
    print("=" * 50 + "\n")
    
    process = subprocess.Popen(
        common.strings(cmd),
        stdout=config.log_f,
        env=config.runtime_env,
        stderr=subprocess.STDOUT,
        text=True
    )
    
    config.log(f"Container command execution started with PID {process.pid}")
    
    while process.poll() is None:
        time.sleep(2)
    
    return_code = process.returncode
    
    if return_code == 0:
        logging.info(f"\nCommand execution completed successfully (return code: {return_code})\n")
    elif return_code == 137:
        raise ExecutionException(f"\nCommand was killed with SIGKILL (exit code 137). Possible OOM.\n")
    else:
        raise ExecutionException(f"\nCommand execution failed with return code: {return_code}\n")


def run_django_tests(
        config: CodingAgentConfig,
        run_locally: bool,
        test_args: list[str] = None
) -> bool:
    # Prepare test command
    test_args = list(test_args or [])
    python_cmd = ["manage.py", "test"] + test_args
    config.dump_env_to_envrc()
    env = os.environ.copy()
    env.update(config.runtime_env)
    db_path = config.sandbox_root_dir / "db.sqlite3"
    env["DATABASE_URL"] = f"sqlite:///{db_path}"

    config.log("\n" + "=" * 80)
    config.log("RUNNING DJANGO TESTS LOCALLY (VENV)")
    config.log("=" * 80 + "\n")
    config.log(f"Command: `python {' '.join(python_cmd)}`")
    config.log(f"Working directory: {config.sandbox_root_dir}\n")

    force_backend_tests = _env_flag("FORCE_RUN_BACKEND_TESTS")
    run_in_local_mode = IterationMode.LOCAL_TESTS.eq(config.self_driving_task.iteration_mode) and not force_backend_tests

    if run_in_local_mode:
        try:
            result = config.git.run_python(python_cmd, env)
            if result.returncode == 0:
                config.log("\n✅ LOCAL DJANGO TESTS PASSED\n")
                return True
            elif result.returncode == 137:
                raise ExecutionException("Test process killed with SIGKILL (exit code 137). Possible OOM.")
            else:
                raise FailingTestException(f"\n❌ LOCAL DJANGO TESTS FAILED (exit code {result.returncode})\n")
        except Exception as e:
            logging.exception(e)
            raise FailingTestException(f"\n❌ LOCAL DJANGO TESTS FAILED ({e})\n")
    else:
        run_django_container_command(
            config=config,
            command_args=["test", "--keepdb", "--noinput", *test_args]
        )

        return True


def run_jest_tests(
        config: CodingAgentConfig,
        frontend_dir: Path
) -> None:
    """Run Jest component tests."""
    config.log("\n" + "=" * 80)
    config.log("RUNNING JEST COMPONENT TESTS")
    config.log("=" * 80 + "\n")
    
    # Determine working directory relative to /app mount
    if True or config.is_ui_first_phase:
        subprocess.run(
            ["npm", "install"],
            env=config.runtime_env,
            stdout=config.log_f,
            cwd=frontend_dir,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=120,
            check=False
        )
        
        process = subprocess.Popen(
            ["npm", "test", "--", "--ci", "--forceExit", "--runInBand", "--detectOpenHandles", "--coverage=false"],
            env=config.runtime_env,
            stdout=config.log_f,
            cwd=frontend_dir,
            stderr=subprocess.STDOUT,
            text=True
        )
        
        while process.poll() is None:
            time.sleep(2)
        
        return_code = process.returncode
        if return_code == 0:
            logging.info(f"\nCommand execution completed successfully (return code: {return_code})\n")
        elif return_code == 137:
            raise ExecutionException(f"\nCommand was killed with SIGKILL (exit code 137). Possible OOM.\n")
        else:
            raise FailingTestException(f"TEST FAILURE:  npm test failed.  see log output for details")
    else:
        work_dir = f"/app/{frontend_dir.relative_to(config.sandbox_root_dir)}" if frontend_dir != config.sandbox_root_dir else "/app"
        run_generic_container_command(
            config=config,
            command=[
                "sh", "-lc",
                "npm install && npm test -- --ci --forceExit --runInBand --detectOpenHandles --coverage=false"
            ],
            working_dir=work_dir
        )
    config.log("✅ Jest component tests PASSED\n")


def run_playwright_tests(
        config: CodingAgentConfig,
        frontend_dir: Path
) -> None:
    """Run Playwright E2E tests."""
    config.log("\n" + "=" * 80)
    config.log("RUNNING PLAYWRIGHT E2E TESTS")
    config.log("=" * 80 + "\n")
    
    work_dir = f"/app/{frontend_dir.relative_to(config.sandbox_root_dir)}" if frontend_dir != config.sandbox_root_dir else "/app"
    
    # Install Playwright browsers if needed (one-time in container)
    config.log("Installing Playwright browsers...")
    try:
        run_generic_container_command(
            config=config,
            command=["npx", "playwright", "install", "--with-deps", "chromium"],
            working_dir=work_dir
        )
    except ExecutionException as e:
        config.log(f"⚠️  Playwright browser installation failed: {e}")
        config.log("⚠️  Attempting to run tests anyway...")
    
    # Run E2E tests
    run_generic_container_command(
        config=config,
        command=["npx", "playwright", "test"],
        working_dir=work_dir
    )
    config.log("✅ Playwright E2E tests PASSED\n")


def run_cypress_tests(
        config: CodingAgentConfig,
        frontend_dir: Path
) -> None:
    """Run Cypress E2E tests."""
    config.log("\n" + "=" * 80)
    config.log("RUNNING CYPRESS E2E TESTS")
    config.log("=" * 80 + "\n")
    
    work_dir = f"/app/{frontend_dir.relative_to(config.sandbox_root_dir)}" if frontend_dir != config.sandbox_root_dir else "/app"
    
    run_generic_container_command(
        config=config,
        command=["npx", "cypress", "run", "--headless", "--browser", "chrome"],
        working_dir=work_dir
    )
    config.log("✅ Cypress E2E tests PASSED\n")


def build_deploy_exec_iteration(config: CodingAgentConfig, attempt=0) -> str:
    """
    Main iteration orchestration with multi-stage execution:
    1. Determine iteration mode based on build planner + current state
    2. Execute appropriate stage: local tests, container tests, or AWS deployment
    3. Transition to next mode if stage succeeds
    """
    deployment_start_epoch = int(time.time())
    
    try:
        config.current_iteration.evaluation_json = None
        config.current_iteration.save()
        task_execution = init_task_execution(config.current_iteration)

        if _uses_repo_backed_task_runtime(config.task):
            execute_iteration(config, task_execution=task_execution)
            config.log("Iteration execution finished")
            return
        
        build_plan = config.get_build_plan(reset=True)
        config._build_plan_overrides = _apply_build_plan_overrides(config, build_plan)
        iteration_mode = determine_iteration_mode(config)
        update_iteration_mode(config, iteration_mode)
        lambda_datas = []
        
        if build_plan.get(BuildStep.BUILD_CONTAINER):
            build_container(config)
        
        if build_plan.get(BuildStep.BUILD_LAMBDAS):
            lambda_datas = build_lambdas(config)
        
        if build_plan.get(BuildStep.PUSH_TO_ECR):
            push_to_ecr(config)
        
        if IterationMode.AWS_DEPLOYMENT.eq(iteration_mode) or build_plan.get(BuildStep.DEPLOY_TO_AWS):
            deploy_stack(
                config=config,
                lambda_datas=lambda_datas
            )
        
        if build_plan.get(BuildStep.MIGRATE_DATABASE):
            migrate_database(config)
        
        execute_iteration(config)
        
        config.log("Iteration execution finished")
    
    except NeedPlan as e:
        raise e
    except BadPlan as e:
        raise e
    except MissingStackPerms as e:
        logging.exception(e)
        raise AgentBlocked({
            "missing_perms": e.missing_permissions
        })
    except AgentBlocked as e:
        raise e
    except OpenTofuException as e:
        config.log(f"OpenTofu deployment error: {e}")
    except FailingTestException as e:
        config.log(f"Tests are failing. **review sysout logs for details**")
    except Exception as e:
        config.log(common.get_stack_trace_as_string(e))
        raise BadPlan(str(e))
    finally:
        store_deploy_and_execution_logs(config, deployment_start_epoch)
        exec_container_prune()
        config.business.snapshot_code(config.current_iteration)


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _apply_build_plan_overrides(config: CodingAgentConfig, build_plan: dict) -> dict[str, bool]:
    overrides = {
        "force_backend_tests": False,
        "force_build_container": False,
    }

    if _env_flag("FORCE_BUILD_CONTAINER"):
        if not build_plan.get(BuildStep.BUILD_CONTAINER):
            config.log("FORCE_BUILD_CONTAINER enabled; overriding build plan to rebuild container")
        build_plan[BuildStep.BUILD_CONTAINER] = True
        build_plan[BuildStep.PUSH_TO_ECR] = True
        overrides["force_build_container"] = True

    if _env_flag("FORCE_RUN_BACKEND_TESTS"):
        if not build_plan.get(BuildStep.RUN_BACKEND_TESTS):
            config.log("FORCE_RUN_BACKEND_TESTS enabled; overriding build plan to run backend tests")
        build_plan[BuildStep.RUN_BACKEND_TESTS] = True
        build_plan[BuildStep.DEPLOY_TO_AWS] = True
        overrides["force_backend_tests"] = True

    return overrides


def store_deploy_and_execution_logs(
        config: CodingAgentConfig,
        deployment_start_epoch: int
):
    config.current_iteration.log_content_cloudwatch = config.stack_manager.get_cloudwatch_content()
    
    deploy_logs = []
    deployment_errors = []
    for run_result in config.stack_manager.run_results:
        deploy_logs.append({
            "stage": run_result.stage,
            "command": run_result.command,
            "started_at": run_result.started_at,
            "completed_at": run_result.completed_at,
            "stdout": run_result.stdout,
        })
        
        if run_result.returncode not in [0, 2, 3]:
            deployment_errors.append({
                "stage": run_result.stage,
                "command": run_result.command,
                "started_at": run_result.started_at,
                "completed_at": run_result.completed_at,
                "stdout": run_result.stdout,
                "stderr": run_result.stderr
            })
    
    config.current_iteration.log_content_deployment = {
        "schema": "opentofu/v1",
        "deployment_window_start": datetime.fromtimestamp(deployment_start_epoch, tz=dt_timezone.utc).isoformat(),
        "deployment_successful": len(deployment_errors) == 0,
        "deployment_logs": deploy_logs,
        "deployment_errors": deployment_errors
    }
    
    config.current_iteration.save(update_fields=["log_content_deployment", "log_content_cloudwatch"])


def execute_iteration(config: CodingAgentConfig, task_execution=None):
    config.set_phase(SdaPhase.EXECUTION)
    
    task = config.task
    task_type = config.task_type
    self_driving_task = config.self_driving_task

    if _uses_repo_backed_task_runtime(task):
        task_execution = task_execution or init_task_execution(config.current_iteration)
        try:
            run_result = _run_repo_backed_task_execution(config, task_execution)
            if run_result.model_metadata is not None:
                merged_model_metadata = {
                    **(task_execution.model_metadata or {}),
                    **run_result.model_metadata,
                }
                TaskExecution.objects.filter(id=task_execution.id).update(
                    model_metadata=merged_model_metadata
                )
                task_execution.model_metadata = merged_model_metadata
            task_execution.resolve(
                output=run_result.output,
                evaluation_score=run_result.evaluation_score,
                evaluation_metadata=run_result.evaluation_metadata,
            )
            _set_repo_backed_iteration_result(
                config,
                task_execution,
                goal_achieved=True,
                summary=f"Executed repo-backed task implementation from {task_execution.implementation_provenance.get('application_repo_file_path')}",
            )
            raise GoalAchieved(config.current_iteration.evaluation_json)
        except GoalAchieved:
            raise
        except AgentBlocked:
            raise
        except Exception as exc:
            task_execution.resolve(
                status=TaskStatus.FAILED,
                error_msg=str(exc),
            )
            blocked_data = {
                "description": f"Repo-backed task execution failed for {task.id}",
                "task_id": task.id,
                "error": str(exc),
                "implementation_provenance": task_execution.implementation_provenance,
            }
            _set_repo_backed_iteration_result(
                config,
                task_execution,
                goal_achieved=False,
                summary=blocked_data["description"],
                blocked_data=blocked_data,
            )
            raise AgentBlocked(blocked_data)
    
    if TaskType.CODING_ML.eq(task_type):
        run_django_container_command(
            config=config,
            command_args=self_driving_task.main_name
        )
        config.log_f.flush()  # Ensure ML execution logs are visible to tailing thread
    elif TaskType.PRODUCTION_DEPLOYMENT.eq(task_type):
        logging.info("Skipping automated test run for production deployment task")
        # TODO - is it possible to block until the new ECS task is running?
    elif TaskType.TASK_EXECUTION.eq(task_type) and TaskExecutionSchedule.ONCE.eq(task.execution_schedule):
        task_io_dir = Path(config.sandbox_root_dir) / "task_io"
        task_io_dir.mkdir(parents=True, exist_ok=True)
        
        input_file = task_io_dir / f"{task.id}-input.json"
        common.write_json(input_file, task.get_upstream_outputs())
        
        output_file = task_io_dir / f"{task.id}-output.json"
        
        run_django_container_command(
            config=config,
            command_args=[
                self_driving_task.main_name,
                "--input_file", input_file,
                "--output_file", output_file
            ]
        )
    elif task_type in [TaskType.CODING_APPLICATION, TaskType.DESIGN_WEB_APPLICATION, TaskType.INITIATIVE_VERIFICATION]:
        command_plan = build_execution_command_plan(config)
        execution_outcome = config.frontier_session.run_commands(command_plan)
        status = execution_outcome.get("status")
        analysis = execution_outcome.get("analysis", {})
        summary = analysis.get("summary") or "Automated command execution outcome unavailable"
        if status == "blocked":
            raise AgentBlocked(analysis.get("blocked") or analysis)
        if status == "failed":
            raise FailingTestException(summary)
        if status == "retry":
            raise RetryableException(summary)
    else:
        logging.info(f"nothing to execute for task type {task_type}")


def detect_frontend_framework(config: CodingAgentConfig) -> dict[str, Any]:
    """
    Detect if sandbox contains React or React Native frontend.

    Returns dict with:
    - has_frontend: bool
    - framework: "react" | "react-native" | None
    - package_json_path: Path | None
    - has_jest: bool
    - has_playwright: bool
    - has_cypress: bool
    - frontend_dir: Path | None (e.g., "frontend/" or root)
    """
    sandbox = Path(config.sandbox_root_dir)
    
    # Check for package.json in common locations
    package_json_candidates = [
        sandbox / "mobile" / "package.json",  # frontend/ subdirectory
        sandbox / "frontend" / "package.json",  # frontend/ subdirectory
        sandbox / "client" / "package.json",  # client/ subdirectory
    ]
    
    for pkg_path in package_json_candidates:
        if not pkg_path.exists():
            continue
        
        try:
            pkg_data = json.loads(pkg_path.read_text())
            deps = {**pkg_data.get("dependencies", {}), **pkg_data.get("devDependencies", {})}
            
            # Detect framework
            framework = None
            if "react-native" in deps:
                framework = "react-native"
            elif "react" in deps or "next" in deps:
                framework = "react"
            
            if not framework:
                continue
            
            # Detect test frameworks
            has_jest = "jest" in deps or "test" in pkg_data.get("scripts", {})
            has_playwright = "@playwright/test" in deps or "playwright" in deps
            has_cypress = "cypress" in deps
            
            return {
                "has_frontend": True,
                "framework": framework,
                "package_json_path": pkg_path,
                "has_jest": has_jest,
                "has_playwright": has_playwright,
                "has_cypress": has_cypress,
                "frontend_dir": pkg_path.parent,
            }
        except (json.JSONDecodeError, OSError) as e:
            config.log(f"Failed to parse {pkg_path}: {e}")
            continue
    
    return {
        "has_frontend": False,
        "framework": None,
        "package_json_path": None,
        "has_jest": False,
        "has_playwright": False,
        "has_cypress": False,
        "frontend_dir": None,
    }


def build_execution_command_plan(config: CodingAgentConfig) -> CommandPlan:
    build_plan = config.get_build_plan()
    overrides = getattr(config, "_build_plan_overrides", None)
    if overrides is None:
        overrides = _apply_build_plan_overrides(config, build_plan)
        config._build_plan_overrides = overrides
    force_backend_tests = overrides.get("force_backend_tests", False)
    config.dump_env_to_envrc()

    iteration_mode = getattr(config.self_driving_task, "iteration_mode", None)
    plan = CommandPlan(
        description="Automated execution command plan",
        context={
            "build_plan": {
                (k.name if isinstance(k, BuildStep) else str(k)): bool(v)
                for k, v in build_plan.items()
                if k != "REASONING"
            },
            "build_reasoning": build_plan.get("REASONING"),
            "iteration_mode": iteration_mode,
        }
    )
    plan.context["previous_test_errors"] = common.get(
        config.iteration_to_modify,
        ["evaluation_json", "test_errors"]
    )

    if not build_plan.get(BuildStep.RUN_FRONTEND_TESTS):
        config.log("RUN_FRONTEND_TESTS=false: Skipping frontend tests")
    else:
        plan.extend(build_frontend_test_commands(config))

    if not build_plan.get(BuildStep.RUN_BACKEND_TESTS):
        config.log("RUN_BACKEND_TESTS=false: Skipping backend tests")
    elif not build_plan.get(BuildStep.DEPLOY_TO_AWS) and not force_backend_tests:
        config.log("RUN_BACKEND_TESTS=true but DEPLOY_TO_AWS=false: Skipping backend tests")
    else:
        plan.extend(build_backend_test_commands(config, build_plan))

    return plan


def build_backend_test_commands(config: CodingAgentConfig, build_plan: dict) -> list[CommandSpec]:
    run_locally = config.task.get_container_image_tag() is None or build_plan.get(BuildStep.RUN_TESTS_LOCALLY, False)

    test_errors_blob = common.get(config.iteration_to_modify, ["evaluation_json", "test_errors"])
    failing_python_tests = [t for t in test_errors_blob if t.get("file_name", "").endswith(".py")] if test_errors_blob else []

    if failing_python_tests:
        first_tests = llm_chat(
            "parse test failures",
            [
                LlmMessage.sys(textwrap.dedent("""
                    Parse the failing python tests from the supplied log output.
                    format the test name as fully qualified test_module.test_method
                    **important** only return the failing python (or django) tests
                    return a list of all failing tests using the following format
                    ```json
                    {
                        "failing_tests": [
                            "core.tests.test_module.TestCase.test_example"
                        ]
                    }
                """)),
                LlmMessage.user_from_data("Tests log output", failing_python_tests)
            ],
            tag_entity=config.current_iteration,
            model=LlmModel.OPENAI_GPT_5_NANO,
            code_response=True
        ).json().get("failing_tests")
    elif config.self_driving_task.test_file_path:
        test_label = config.self_driving_task.test_file_path
        if test_label.endswith(".py"):
            first_tests = [
                test_label.replace("/", ".").removesuffix(".py").lstrip(".")
            ]
        else:
            first_tests = None
    else:
        first_tests = None

    commands: list[CommandSpec] = []
    if first_tests:
        config.log(f"Planning targeted backend tests: {first_tests}")
        commands.append(_build_django_command_spec(
            label="targeted_backend_tests",
            test_args=first_tests,
            run_locally=run_locally
        ))

    commands.append(_build_django_command_spec(
        label="full_backend_tests",
        test_args=[],
        run_locally=run_locally
    ))

    return commands


def build_frontend_test_commands(config: CodingAgentConfig) -> list[CommandSpec]:
    frontend_info = detect_frontend_framework(config)
    if not frontend_info["has_frontend"]:
        return []

    frontend_dir = frontend_info["frontend_dir"] or config.sandbox_root_dir
    metadata_base = {
        "type": "frontend_tests",
        "framework": frontend_info["framework"],
        "frontend_dir": str(frontend_dir.relative_to(config.sandbox_root_dir)) if frontend_dir else "",
    }

    if not any([frontend_info["has_jest"], frontend_info["has_playwright"], frontend_info["has_cypress"]]):
        raise BadPlan(textwrap.dedent(f"""
            ⚠️  No E2E test framework found (Playwright/Cypress/Jest) - skipping E2E tests.  Please update {frontend_dir}/package.json to include a testing framework
            ⚠️  WARNING: Per codeplanner--test_driven_development.md, E2E tests are REQUIRED for React projects
        """))

    commands: list[CommandSpec] = []
    if frontend_info["has_jest"]:
        commands.append(CommandSpec(
            label="jest_component_tests",
            shell_command=[
                "npm", "test", "--",
                "--ci", "--forceExit", "--runInBand", "--detectOpenHandles", "--coverage=false"
            ],
            workdir=frontend_dir,
            metadata={**metadata_base, "suite": "jest", "frontend_dir_abs": str(frontend_dir)},
            action=_jest_command_action
        ))

    if frontend_info["has_playwright"]:
        commands.append(CommandSpec(
            label="playwright_e2e_tests",
            shell_command=["npx", "playwright", "test"],
            workdir=frontend_dir,
            metadata={**metadata_base, "suite": "playwright", "frontend_dir_abs": str(frontend_dir)},
            action=_playwright_command_action
        ))

    if frontend_info["has_cypress"]:
        commands.append(CommandSpec(
            label="cypress_e2e_tests",
            shell_command=["npx", "cypress", "run", "--headless", "--browser", "chrome"],
            workdir=frontend_dir,
            metadata={**metadata_base, "suite": "cypress", "frontend_dir_abs": str(frontend_dir)},
            action=_cypress_command_action
        ))

    return commands


def _build_django_command_spec(label: str, test_args: list[str], run_locally: bool) -> CommandSpec:
    command = ["python", "manage.py", "test"] + common.ensure_list(test_args)
    metadata = {
        "type": "django_tests",
        "run_locally": run_locally,
        "test_args": list(common.ensure_list(test_args)),
    }
    return CommandSpec(
        label=label,
        shell_command=command,
        metadata=metadata,
        action=_execute_django_command
    )


def _execute_django_command(config: CodingAgentConfig, spec: CommandSpec) -> CommandResult:
    start = time.time()
    test_args = spec.metadata.get("test_args") or []
    run_locally = spec.metadata.get("run_locally", True)
    try:
        run_django_tests(config, run_locally, test_args)
        if not test_args and not config.self_driving_task.initial_tests_pass:
            config.self_driving_task.initial_tests_pass = True
            config.self_driving_task.save()
        return CommandResult(
            label=spec.label,
            shell_command=list(spec.shell_command),
            returncode=0,
            stdout="Command output streamed to execution log",
            stderr="",
            duration_seconds=time.time() - start,
            metadata=spec.metadata,
        )
    except Exception as exc:  # noqa: BLE001
        return CommandResult(
            label=spec.label,
            shell_command=list(spec.shell_command),
            returncode=1,
            stdout="",
            stderr=str(exc),
            duration_seconds=time.time() - start,
            metadata={**spec.metadata, "exception": type(exc).__name__},
        )


def _jest_command_action(config: CodingAgentConfig, spec: CommandSpec) -> CommandResult:
    frontend_dir = Path(spec.metadata.get("frontend_dir_abs") or config.sandbox_root_dir)
    return _execute_frontend_runner(config, spec, lambda: run_jest_tests(config, frontend_dir))


def _playwright_command_action(config: CodingAgentConfig, spec: CommandSpec) -> CommandResult:
    frontend_dir = Path(spec.metadata.get("frontend_dir_abs") or config.sandbox_root_dir)
    return _execute_frontend_runner(config, spec, lambda: run_playwright_tests(config, frontend_dir))


def _cypress_command_action(config: CodingAgentConfig, spec: CommandSpec) -> CommandResult:
    frontend_dir = Path(spec.metadata.get("frontend_dir_abs") or config.sandbox_root_dir)
    return _execute_frontend_runner(config, spec, lambda: run_cypress_tests(config, frontend_dir))


def _execute_frontend_runner(
        config: CodingAgentConfig,
        spec: CommandSpec,
        runner: Callable[[], None]
) -> CommandResult:
    start = time.time()
    try:
        runner()
        return CommandResult(
            label=spec.label,
            shell_command=list(spec.shell_command),
            returncode=0,
            stdout="Command output streamed to execution log",
            stderr="",
            duration_seconds=time.time() - start,
            metadata=spec.metadata,
        )
    except Exception as exc:  # noqa: BLE001
        return CommandResult(
            label=spec.label,
            shell_command=list(spec.shell_command),
            returncode=1,
            stdout="",
            stderr=str(exc),
            duration_seconds=time.time() - start,
            metadata={**spec.metadata, "exception": type(exc).__name__},
        )


def _run_manage_command_action(config: CodingAgentConfig, spec: CommandSpec) -> CommandResult:
    start = time.time()
    manage_args = list(spec.metadata.get("manage_args") or [])
    try:
        run_django_container_command(
            config=config,
            command_args=manage_args
        )
        return CommandResult(
            label=spec.label,
            shell_command=list(spec.shell_command),
            returncode=0,
            stdout="Command output streamed to execution log",
            stderr="",
            duration_seconds=time.time() - start,
            metadata=spec.metadata,
        )
    except Exception as exc:  # noqa: BLE001
        return CommandResult(
            label=spec.label,
            shell_command=list(spec.shell_command),
            returncode=1,
            stdout="",
            stderr=str(exc),
            duration_seconds=time.time() - start,
            metadata={**spec.metadata, "exception": type(exc).__name__},
        )


def determine_iteration_mode(config: CodingAgentConfig) -> IterationMode:
    """
    Determines which iteration mode to use based on:
    - Current iteration_mode
    - Build planner decisions
    - Test pass status

    State transitions:
    - LOCAL_TESTS → CONTAINER_TESTS: When local tests pass AND (stack changed OR container needed)
    - LOCAL_TESTS → AWS_DEPLOYMENT: When local tests pass AND deployment needed
    - CONTAINER_TESTS → AWS_DEPLOYMENT: When container tests pass
    - AWS_DEPLOYMENT → LOCAL_TESTS: After successful deployment (reset for next task iteration)

    Args:
        config: CodingAgentConfig

    Returns:
        IterationMode enum value
    """
    current_mode = config.self_driving_task.iteration_mode
    
    # If UI-first phase, skip this entirely
    if config.is_ui_first_phase:
        return IterationMode.LOCAL_TESTS
    
    # Force AWS deployment if stack.tf changed or DB migration required
    build_plan = config.get_build_plan()
    if build_plan.get(BuildStep.UPDATE_STACK_TF) or build_plan.get(BuildStep.MIGRATE_DATABASE):
        config.log("Stack or DB changes detected - forcing AWS_DEPLOYMENT mode")
        return IterationMode.AWS_DEPLOYMENT
    
    # If build planner says we can run tests locally, stay in LOCAL_TESTS
    if build_plan.get(BuildStep.RUN_TESTS_LOCALLY):
        config.log("Only code/test changes - using LOCAL_TESTS mode")
        return IterationMode.LOCAL_TESTS
    
    # If currently in LOCAL_TESTS and tests passed
    if IterationMode.LOCAL_TESTS.eq(current_mode):
        if config.self_driving_task.initial_tests_pass:
            # Transition to container verification
            config.log("Local tests passed - transitioning to CONTAINER_TESTS mode")
            return IterationMode.CONTAINER_TESTS
        else:
            # Stay in local mode until tests pass
            config.log("Staying in LOCAL_TESTS mode (tests not yet passing)")
            return IterationMode.LOCAL_TESTS
    
    # If in CONTAINER_TESTS and build planner says deploy
    if IterationMode.CONTAINER_TESTS.eq(current_mode):
        if build_plan.get(BuildStep.DEPLOY_TO_AWS):
            config.log("Container tests ready - transitioning to AWS_DEPLOYMENT mode")
            return IterationMode.AWS_DEPLOYMENT
        else:
            config.log("Staying in CONTAINER_TESTS mode")
            return IterationMode.CONTAINER_TESTS
    
    # If in AWS_DEPLOYMENT, stay there until task completes
    if IterationMode.AWS_DEPLOYMENT.eq(current_mode):
        config.log("In AWS_DEPLOYMENT mode")
        return IterationMode.AWS_DEPLOYMENT
    
    # Default: start with local tests
    config.log("No mode set - defaulting to LOCAL_TESTS")
    return IterationMode.LOCAL_TESTS


def update_iteration_mode(config: CodingAgentConfig, new_mode: IterationMode):
    """
    Updates the iteration_mode in the database and logs the transition.

    Args:
        config: CodingAgentConfig
        new_mode: New IterationMode to set
    """
    old_mode = config.self_driving_task.iteration_mode
    
    if old_mode != new_mode.value:
        config.log(f"\n{'=' * 80}")
        config.log(f"ITERATION MODE TRANSITION: {old_mode} → {new_mode.value}")
        config.log(f"{'=' * 80}\n")
        
        config.self_driving_task.iteration_mode = new_mode.value
        config.self_driving_task.save(update_fields=["iteration_mode"])


def ingest_tofu_ouputs(config: CodingAgentConfig):
    """Populate runtime_env with the OpenTofu outputs required by the runtime."""
    outputs = config.stack_manager.get_outputs()
    business = config.initiative.business

    config.frontier_session.patch_iac({
        "operation": "ingest_tofu_outputs",
        "outputs": outputs,
        "env": config.env_type.value,
    })
    
    for output_name, output_value in outputs.items():
        output_value = str(outputs.get(output_name) or "")
        config.runtime_env[
            common.camel_to_snake(output_name).upper()
        ] = str(output_value)
    
    for env_name, output_name in ENVVAR_TO_STACK_OUTPUT.items():
        output_value = str(outputs.get(output_name) or "")
        if output_value:
            config.runtime_env[env_name.upper()] = output_value
    
    missing_outputs = []
    for credential_service, secret_arn in config.stack.get_credential_arns(CredentialServiceProvisioning.STACK_GENERATED):
        output_name, env_name = credential_service.get_stackoutput_and_env_names()
        output_value = str(outputs.get(output_name) or "")
        if not output_value:
            missing_outputs.append(credential_service)
            continue
        
        config.runtime_env[env_name.upper()] = output_value
        
        credential_arns = business.credential_arns or {}
        credential_arns[credential_service.value] = output_value
        
        business.credential_arns = credential_arns
        business.save(update_fields=["credential_arns"])
    
    if missing_outputs:
        raise BadPlan("".join([
            textwrap.dedent(f"""
                ERROR - Planner please fix:
                ```
                    stack.tf lacks an output value for the required output named `{credential_service.get_stackoutput_var()}`  
                    - This output value should the arn to the secret that defines the `{credential_service}` credentials.  
                    - The secret defined by the `{credential_service.get_stackoutput_var()}` arn must have values for the following keys: {credential_service.get_secret_keys()}
                    - stack.tf needs to define this secret with values for the keys {credential_service.get_secret_keys()} and return the arn to the secret as an output variable named `{credential_service.get_stackoutput_var()}`
                    - if the planner and/or codewriter is unable to define these credential entirely in the stack (like it needs external input), it should return as `blocked`.  This should be a last resort though, try to define the values in the stack
                ```
        """) for credential_service in missing_outputs
        ]))


def template_modified_this_iteration(
        config: CodingAgentConfig,
        cloudformation_template: InfrastructureStackType
) -> bool:
    code_file = config.business.codefile_set.filter(
        file_path=cloudformation_template.name
    ).first()
    iteration = config.current_iteration
    if not code_file or not iteration:
        return False
    
    code_version = iteration.codeversion_set.filter(
        code_file=code_file
    ).order_by("created_at").last()
    
    return bool(code_version and code_version.get_diff())


def is_lambdas_modified(config: CodingAgentConfig):
    iteration = config.current_iteration
    
    lambda_source_files = [
        lambda_data['code_file_path']
        for lambda_data in get_stack_lambdas(config)
    ]
    
    if not lambda_source_files:
        return False
    
    return any(
        cv.get_diff() for cv in iteration.codeversion_set.filter(
            code_file__file_path__in=lambda_source_files
        )
    )


def migrate_database(config: CodingAgentConfig):
    if not config.stack_manager.get_db_is_running():
        logging.info("skipping database migration - database is not running")
        return
    
    ingest_tofu_ouputs(config)
    migration_plan = CommandPlan(
        description="Database migrations",
        context={"operation": "migrate_database"}
    )
    migration_plan.extend([
        CommandSpec(
            label="makemigrations",
            shell_command=["python", "manage.py", "makemigrations", "--noinput"],
            metadata={"type": "django_management", "manage_args": ["makemigrations", "--noinput"]},
            action=_run_manage_command_action
        ),
        CommandSpec(
            label="migrate",
            shell_command=["python", "manage.py", "migrate"],
            metadata={"type": "django_management", "manage_args": ["migrate"]},
            action=_run_manage_command_action
        )
    ])

    migration_result = config.frontier_session.run_commands(migration_plan)
    status = migration_result.get("status")
    summary = migration_result.get("analysis", {}).get("summary", "Database migration failed")
    if status == "blocked":
        raise AgentBlocked(migration_result.get("analysis", {}))
    if status == "retry":
        raise RetryableException(summary)
    if status == "failed":
        raise DatabaseMigrationException(summary)


def validate_web_container(config: CodingAgentConfig):
    ingest_tofu_ouputs(config)
    import socket
    
    port = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', 0))
            port = s.getsockname()[1]
    except Exception:
        port = settings.VALIDATION_PORT
    
    process = None
    config.log(f"==========  BEGIN Webcontainer Validation ===============")
    config.frontier_session.patch_iac({
        "operation": "validate_web_container",
        "image_tag": config.task.get_container_image_tag(),
        "env": config.env_type.value,
    })
    try:
        process = subprocess.Popen(
            [
                "podman", "run", "--rm",
                "--memory", "4g",
                "--memory-swap", "10g",
                "-e", f"HTTP_LISTENER_PORT={port}",
                "--platform", ContainerPlatform.FARGATE,
                "-p", f"{port}:{port}",
                "-v", f"{config.sandbox_root_dir}:/app",
                "-w", "/app",
                *build_env_flags(config.runtime_env),
                common.assert_not_empty(config.task.get_container_image_tag())
            ],
            stdout=config.log_f,
            stderr=subprocess.STDOUT,
            env=config.runtime_env,
            text=True
        )
        
        config.log(f"Starting Webcontainer Validation (PID={process.pid})")
        start_time = time.time()
        max_wait = 10 * 60  # seconds
        healthy = False
        for attempt in range(1, 100):  # will hit max_wait before 100
            time.sleep(5)
            config.log(f"Healthcheck attempt {attempt}: querying http://127.0.0.1:{port}/health/")
            try:
                res = subprocess.run(
                    ["curl", "-fsSL", f"http://127.0.0.1:{port}/health/"],
                    stdout=config.log_f,
                    stderr=subprocess.STDOUT
                )
                if res.returncode == 0:
                    config.log(f"Healthcheck succeeded on attempt {attempt}")
                    healthy = True
                    break
                else:
                    config.log(f"Healthcheck attempt {attempt} failed (curl exit {res.returncode})")
            except Exception as e:
                config.log(f"Healthcheck attempt {attempt} exception: {e}")
            if time.time() - start_time > max_wait:
                break
        
        if healthy:
            config.log("Webcontainer validation completed successfully")
        else:
            raise BadPlan("Webcontainer validation failed.  see error logs for details")
    finally:
        try:
            config.log("terminating healthcheck container")
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        except Exception as e:
            logging.exception(e)
            logging.error("failed to kill the healthcheck container")
        
        config.log(f"========== END Webcontainer Validation ===============")


def init_task_execution(iteration):
    task = iteration.self_driving_task.task
    if TaskType.TASK_EXECUTION.neq(task.task_type):
        return None
    
    task_input = {}
    for upstream_task in task.depends_on.all():
        if not TaskStatus.COMPLETE.eq(upstream_task.status):
            raise AgentBlocked(f"task {task.id} depends on task {upstream_task.id}, but the upstream task's status is {upstream_task.status}")
        
        if TaskType.TASK_EXECUTION.eq(iteration.self_driving_task.task.task_type):
            previous_task_execution = upstream_task.get_last_execution()
            if not previous_task_execution:
                raise AgentBlocked({
                    "description": f"task {task.id} depends on upstream task {upstream_task.id}, but the upstream task has not executed"
                })
            
            task_input[upstream_task.id] = previous_task_execution.output
    
    return task.create_execution(
        input_data=task_input,
        iteration=iteration
    )


def assert_tests_green(config: CodingAgentConfig):
    test_reviewer_output = llm_chat(
        "Assert Initial Tests Green",
        [
            get_sys_prompt("test_reviewer.md"),
            config.get_log_content()
        ],
        output_schema="test_reviewer.md.schema.json",
        tag_entity=config.current_iteration,
        reasoning_effort=LlmReasoningEffort.LOW
    ).json()
    
    if not test_reviewer_output.get("all_passed"):
        raise AgentBlocked({
            "description": "assert_tests_green failed",
            **test_reviewer_output
        })


def evaluate_iteration(
        config: CodingAgentConfig
):
    iteration = config.current_iteration

    log_output = config.set_phase(SdaPhase.EVALUATE)
    if "no space left on device" in common.default_str(log_output).lower():
        exec_container_prune(aggressive=True)
        raise RetryableException(f"execution is failing with 'no space left on device'\n\n{log_output}.  I just pruned the containers, so should be cleared up now.")

    if config.one_off_action:
        goal_achieved_critera = textwrap.dedent(f"""
            **If the code writing or docker build steps failed, set `"goal_achieved": false`.**  
            **if the OpenTofu plan/apply did not complete successfully, set `"goal_achieved": false`.**  

           **Primary decision rule (test-centric):**
           - Set `"goal_achieved": true` when **all** of the following are true:
             - Build/packaging steps completed successfully (no unrecovered build/compiler errors).
             - Infrastructure deployment (e.g., OpenTofu plan/apply) either succeeded or was not invoked; there are no final plan/apply failures.
           - Set `"goal_achieved": false` when **any** of the above conditions are not met (e.g., build/deploy failure, tests did not run, or the test runner reports failing/erroring tests).

           **Treatment of log errors and simulated failures:**
           - Do **not** set `goal_achieved` to `false` **solely** because log output contains stack traces, exceptions, or error messages **if** the relevant tests ultimately pass. Tests may intentionally simulate failures and assert correct handling; such logged errors are acceptable when the test framework reports success.
           - Continue to record important runtime or infra issues in the `error` or `test_errors` fields so downstream agents see them, but let the **test outcomes** drive the `goal_achieved` flag.
           - **Assume negative-path assertions are intentional:** When a passing test suite includes log lines with exceptions, stack traces, warnings, or error messages that are raised and handled inside tests or application code, assume these are part of intentional negative‑case coverage. **Never** set `goal_achieved: false` solely because such errors appear in the logs if all build/deploy steps and tests have succeeded. You may still record these details in `error` or `test_errors` for diagnostics, but they must not influence the `goal_achieved` flag when tests pass.

           **Do not infer cross-iteration state:**
           - Base this determination only on the current iteration's logs and test results; ignore earlier iterations.
        """)
    
    else:
        goal_achieved_critera = textwrap.dedent(f"""
            **If the code writing or docker build steps failed, set `"goal_achieved": false`.**  
            **if the OpenTofu plan/apply did not complete successfully, set `"goal_achieved": false`.**  
            **If the test output shows "Ran 0 tests", set `"goal_achieved": false`.**  
            **If any tests were skipped, set 'goal_achieved': false. Goal can only be set to true if all tests ran successfully without skips and the acceptance criteria are fully covered by the test suite.**  
            - Set `"goal_achieved": true` only if the logs contain no errors, the task output clearly meets the stated GOAL, and test logs show that one or more tests were actually run.  
            - If any errors or incomplete behaviors are detected in the logs, set `"goal_achieved": false`.  
            - Base this determination only on the current logs—do not consider prior iterations.
            
           **Primary decision rule (test-centric):**
           - Set `"goal_achieved": true` when **all** of the following are true:
             - Build/packaging steps completed successfully (no unrecovered build/compiler errors).
             - Infrastructure deployment (e.g., OpenTofu plan/apply) either succeeded or was not invoked; there are no final plan/apply failures.
             - The test runner executed **one or more tests** (i.e., not `Ran 0 tests`).
             - The final test result for the executed suite shows **no failing or erroring tests** (status equivalent to OK / PASSED in the chosen framework).
           - Set `"goal_achieved": false` when **any** of the above conditions are not met (e.g., build/deploy failure, tests did not run, or the test runner reports failing/erroring tests).

           **Treatment of log errors and simulated failures:**
           - Do **not** set `goal_achieved` to `false` **solely** because log output contains stack traces, exceptions, or error messages **if** the relevant tests ultimately pass. Tests may intentionally simulate failures and assert correct handling; such logged errors are acceptable when the test framework reports success.
           - Continue to record important runtime or infra issues in the `error` or `test_errors` fields so downstream agents see them, but let the **test outcomes** drive the `goal_achieved` flag.
           - **Assume negative-path assertions are intentional:** When a passing test suite includes log lines with exceptions, stack traces, warnings, or error messages that are raised and handled inside tests or application code, assume these are part of intentional negative‑case coverage. **Never** set `goal_achieved: false` solely because such errors appear in the logs if all build/deploy steps and tests have succeeded. You may still record these details in `error` or `test_errors` for diagnostics, but they must not influence the `goal_achieved` flag when tests pass.

           **Skipped tests policy:**
           - If organizational policy requires, you may treat skipped tests as a reason to keep `goal_achieved: false`. Otherwise, treat a run with skips but no failures/errors as `goal_achieved: true` **only when explicitly instructed by higher-level configuration or input fields.**

           **Do not infer cross-iteration state:**
           - Base this determination only on the current iteration's logs and test results; ignore earlier iterations.
        """)
        
    summary_inputs = build_iteration_summary_inputs(
        config,
        goal_achieved_critera
    )
    eval_data = config.frontier_session.summarize_iteration(summary_inputs)

    if eval_data.get("exceptions") is not None:
        iteration.exceptions = eval_data.get("exceptions")
        iteration.save(update_fields=["exceptions"])

    if eval_data.get("is_stagnating"):
        config.current_iteration.strategic_unblocking_json = get_strategic_unblocking_data(config)
        config.current_iteration.save()

    allow_goal_achieved = eval_data.get("allow_goal_achieved", False)
    if not allow_goal_achieved:
        eval_data['goal_achieved'] = False

    with transaction.atomic():
        SelfDrivingTaskIteration.objects.filter(id=iteration.id).update(
            evaluation_json=eval_data
        )
        iteration.refresh_from_db(fields=["evaluation_json"])

    if eval_data.get("blocked"):
        raise AgentBlocked(eval_data)

    if common.parse_bool(eval_data.get("goal_achieved")):
        raise GoalAchieved(eval_data)
    
    if not TaskType.CODING_ML.eq(config.task_type):
        # with non ML tasks, we always must move on from the latest
        # the reason for this is with db schema changes - these do not rollback if the code rolls back
        # so, in cases where we might upgrade the schema, always roll forward
        selection_data = {
            "iteration_id_to_modify": "latest",
            "best_iteration_id": str(config.current_iteration.id),
            "previous_iteration_count": 5
        }
    else:
        selection_data = llm_chat(
            "Iteration Selector",
            [
                get_sys_prompt([
                    "iteration_selector.md",
                    "common--iam_role_tofu.md",
                    "common--agent_provided_functionality_tofu.md",
                    "common--domain_management_tofu.md",
                    "common--forbidden_actions_tofu.md",
                    "common--environment_variables_tofu.md"
                ], replacements=[
                    ("<env_vars>", get_env_var_names(config)),
                ]),
                get_goal_msg(config, "Task Goal"),
                get_previous_iteration_summaries_msg(
                    config
                ),
                get_previous_iteration_summaries_msg(
                    config
                ),
                build_previous_iteration_context_messages(
                    config
                )
            ],
            tag_entity=config.current_iteration,
            output_schema="iteration_selector.md.schema.json"
        ).json()
        
        blocked_data = selection_data.get("blocked")
        if blocked_data:
            raise AgentBlocked(blocked_data)
        
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

    _record_iteration_lessons(config, eval_data)

    return eval_data


def extract_exceptions(
        config: CodingAgentConfig,
        iteration: SelfDrivingTaskIteration
):
    deployment_errors = common.get(iteration.log_content_deployment, "deployment_errors")
    runtime_errors = common.get(iteration.log_content_cloudwatch, "errors")
    
    error_analysis = llm_chat(
        "Error Extraction",
        [
            get_sys_prompt("log_parser_tofu.md"),
            LlmMessage.user_from_data("Context", {
                "deployment_logs_exist": bool(iteration.log_content_deployment),
                "version_number": config.current_iteration.version_number if TaskType.PRODUCTION_DEPLOYMENT.neq(config.task_type) else 100,
                "test_file_exists": bool(config.self_driving_task.test_file_path) if TaskType.PRODUCTION_DEPLOYMENT.neq(config.task_type) else True
            }),
            LlmMessage.user_from_data("Logs", {
                "Deployment Errors": deployment_errors,
                "Runtime Errors (cloudwatch)": runtime_errors,
                "Runtime Logs (other)": iteration.log_content_execution or ""
            })
        ],
        model=LlmModel.OPENAI_GPT_5_MINI,
        tag_entity=config.current_iteration,
        output_schema="log_parser_tofu.md.schema.json"
    ).json()
    
    # Extract values from error analysis
    iteration.exceptions = error_analysis.get('exceptions', '')
    iteration.save()
    
    return runtime_errors, deployment_errors, error_analysis


def get_logs_msg(config, iteration):
    return LlmMessage.user_from_data(
        "Execution Logs",
        {
            "exceptions": iteration.exceptions or "No Exceptions Found",
            "sysout logs by Phase": {
                "init": iteration.log_content_init or "N/A",
                "coding": iteration.log_content_coding or "N/A",
                "execution": iteration.log_content_execution or "N/A",
                "evaluation": iteration.log_content_evaluation or "N/A"
            }
        }
    )


def build_iteration_summary_inputs(
        config: CodingAgentConfig,
        goal_policy_text: str
) -> dict[str, Any]:
    iteration = config.current_iteration
    task = config.task
    return {
        "goal": {
            "description": task.description,
            "acceptance_criteria": task.completion_criteria,
            "risk_notes": task.risk_notes,
            "debug_steps": task.debug_steps,
        },
        "goal_achieved_policy": goal_policy_text,
        "allow_goal_achieved_hints": {
            "deployment_logs_exist": bool(iteration.log_content_deployment),
            "version_number": iteration.version_number
        },
        "code_changes": _collect_code_change_data(config),
        "previous_iteration_summaries": _collect_previous_iteration_summaries_for_context(config),
        "logs": _collect_iteration_logs(iteration),
        "deployment": iteration.log_content_deployment,
        "cloudwatch": iteration.log_content_cloudwatch,
        "exceptions": iteration.exceptions,
    }


def _collect_code_change_data(config: CodingAgentConfig) -> list[dict[str, Any]]:
    iteration = config.current_iteration
    return [
        {
            "path": cv.code_file.file_path,
            "diff": cv.get_diff()
        }
        for cv in iteration.codeversion_set.all()
    ]


def _collect_iteration_logs(iteration: SelfDrivingTaskIteration) -> dict[str, Any]:
    return {
        "init": iteration.log_content_init or "",
        "coding": iteration.log_content_coding or "",
        "execution": iteration.log_content_execution or "",
        "evaluation": iteration.log_content_evaluation or "",
    }


def _collect_previous_iteration_summaries_for_context(
        config: CodingAgentConfig
) -> list[dict[str, Any]]:
    iteration = config.current_iteration
    summaries = []
    iterations = list(
        config.self_driving_task.selfdrivingtaskiteration_set.filter(
            evaluation_json__isnull=False
        ).order_by("timestamp")
    )
    for prev_iter in iterations[-5:]:
        if prev_iter == iteration:
            continue
        summary_text = common.get(prev_iter.evaluation_json, "summary")
        if summary_text:
            summaries.append({
                "iteration_id": prev_iter.id,
                "timestamp": prev_iter.timestamp.isoformat() if prev_iter.timestamp else None,
                "summary": summary_text,
            })
    return summaries


def _record_iteration_lessons(config: CodingAgentConfig, eval_data: Mapping[str, Any]) -> None:
    for lesson_data in common.ensure_list(eval_data.get("lessons", [])):
        if not isinstance(lesson_data, Mapping):
            continue
        try:
            AgentLesson.create_from_data(
                "code deploy and execution",
                lesson_data,
                config.current_iteration
            )
        except Exception as exc:  # noqa: BLE001
            logging.exception(exc)


def get_code_changes_msg(config):
    iteration = config.current_iteration
    diffs = []
    for cv in iteration.codeversion_set.all():
        diffs.append({
            "path": cv.code_file.file_path,
            "diff": cv.get_diff()
        })
    
    return LlmMessage.user_from_data("Code changes in this iteration", diffs, "code_file")


def get_previous_iteration_summaries_msg(config):
    iteration = config.current_iteration
    
    return LlmMessage.user_from_data(
        f"**All Previous Iteration Summaries**  try to not to repeat these same errors ",
        [
            {
                "iteration_id": prev_iter.id,
                "iteration_is_current_iteration": prev_iter == iteration,
                "iteration_timestamp": prev_iter.timestamp,
                "iteration_summary": prev_iter.evaluation_json.get("summary"),
            }
            for prev_iter in config.self_driving_task.selfdrivingtaskiteration_set.filter(evaluation_json__isnull=False).order_by("timestamp") if prev_iter.evaluation_json.get("summary")
        ],
        "iteration_summary"
    )


def build_opentofu_plan_context_messages(config: CodingAgentConfig, title=None) -> list[LlmMessage]:
    iteration_to_modify = config.iteration_to_modify
    if not iteration_to_modify:
        return []
    
    iteration_logs = common.get(iteration_to_modify, ["cloudformation_logs", "stacks"])
    if not isinstance(iteration_logs, Mapping) or not iteration_logs:
        return []
    
    stack_summaries: list[dict[str, Any]] = []
    for stack_name, stack_log in iteration_logs.items():
        if not isinstance(stack_log, Mapping):
            continue
        plan_summary = stack_log.get("plan_summary") or {}
        if not plan_summary:
            continue
        stack_summaries.append({
            "stack": stack_name,
            "plan_summary": plan_summary,
        })
    
    if not stack_summaries:
        return []
    
    return LlmMessage.user_from_data(
        title or "OpenTofu Plan Summary",
        {"stacks": stack_summaries}
    )


def build_previous_iteration_context_messages(
        config: CodingAgentConfig,
        title=None
) -> list[LlmMessage]:
    current_iteration = config.current_iteration
    previous_iteration = config.previous_iteration
    iteration_to_modify = config.iteration_to_modify
    
    eval_json = config.self_driving_task.get_most_recent_iteration().evaluation_json or {}
    previous_iteration_count = eval_json.get("previous_iteration_count", 3)
    
    all_iterations = list(
        config.self_driving_task.selfdrivingtaskiteration_set.exclude(
            id=current_iteration.id
        ).filter(
            evaluation_json__isnull=False
        ).order_by("timestamp")
    )
    
    return get_iteration_eval_llm_messages(
        config,
        all_iterations[-previous_iteration_count:],
        title=title
    )


def get_iteration_eval_llm_messages(
        config: CodingAgentConfig,
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
    ).order_by("-timestamp")[:5][::-1]:
        description = ""
        if iteration == previous_iteration and previous_iteration != iteration_to_modify:
            description = "Evalutation of the execution of a previous iteration of the code"
        elif iteration == iteration_to_modify:
            description = "We are rolling the code back to this iteration. This is the evalutation of the execution of iteration of the code we are rolling back to.  We will start our new changes from this code"
        else:
            description = "Previous iteration evaluation.  This is not the previous iteration neither is it the iteration we are rolling back to"
        
        messages.append(
            iteration.get_llm_data(
                description,
                include_details=iteration.id == iteration_to_modify.id
            )
        )
    
    return LlmMessage.user_from_data(
        title,
        messages,
        item_name="previous_iteration_analyses"
    )


def get_architecture_docs(initiative: Initiative):
    return [
        textwrap.dedent(f"""
        # Required Alignment
        All code **must** comport to this architecture
        
        ## Architecture
        {initiative.architecture or initiative.business.architecture}
        """),
        
        textwrap.dedent(f"""
        ## User Documentation / Help Docs (**note** domain names in the docs may reference the top level business domain.  task level domains are usually subdomains - unless you're pushing to production env, use the subdomain)
        {initiative.user_documentation}
        """) if initiative.user_documentation else None
    ]


def perform_code_review(
        config: CodingAgentConfig,
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
                "common--credentials_architecture_tofu.md"
            ]
        ),
        get_architecture_docs(
            config.initiative
        ),
        get_tombstone_message(
            config
        ),
        get_file_structure_msg(
            config.sandbox_root_dir
        ) if not iteration_to_modify.has_error() else [],
        get_previous_iteration_summaries_msg(
            config
        ),
        get_lessons_msg(
            "Relevant past lessons",
            config
        ),
        get_guidance_msg(
            config
        ),
        LlmMessage.user_from_data(
            "Code Review Input: Proposed Code Changes for Current Iteration",
            [
                cv.get_llm_message_data()
                for cv in current_iteration.get_all_code_versions()
            ]
        ),
        f'''The code changes to review are in support of the following goal:

        # Goal
        {task.description}

        # Acceptance Criteria
        {task.completion_criteria or 'none'}

        # Risk Notes
        {task.risk_notes or 'none'}
        ''',
        "Please perform the code review"
    ]
    
    code_review_data = llm_chat(
        "Perform Code Review",
        messages,
        tag_entity=config.current_iteration,
        output_schema="codereviewer.md.schema.json"
    ).json()
    
    config.log(code_review_data)
    
    blocking_issues = code_review_data.get("blocking_issues", [])
    non_blocking_warnings = code_review_data.get("non_blocking_warnings", [])
    if blocking_issues:
        raise CodeReviewException(code_review_data)
    elif non_blocking_warnings:
        config.log(non_blocking_warnings)


def get_lessons_msg(
        title: str,
        config,
        task_desc=None,
        all_lessons=True,
        exclude_invalid=True,
        skip=False
) -> list[LlmMessage]:
    if skip:
        return []
    
    return LlmMessage.user_from_data(
        title,
        get_lessons(
            config,
            task_desc,
            all_lessons,
            exclude_invalid,
            skip
        ),
        item_name="lesson"
    )


def get_lessons(
        config,
        task_desc=None,
        all_lessons=True,
        exclude_invalid=True, skip=False
) -> list[LlmMessage]:
    if skip:
        return []
    
    if all_lessons:
        lessons_q = AgentLesson.objects.all()
    else:
        import numpy as np  # Ensure this is at the top of the file if not already imported
        # Load embedding model (should ideally be cached/shared elsewhere)
        query_embedding = get_text_embedding(task_desc)
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
            lesson_embeddings = get_text_embedding(lesson_texts)
        else:
            lesson_embeddings = []
        
        # Deduplicate by semantic similarity
        seen = []
        unique_indices = []
        
        for i, emb in enumerate(lesson_embeddings):
            if all(util.cos_sim(emb, lesson_embeddings[j]) < 0.92 for j in unique_indices):
                unique_indices.append(i)
        
        lessons = [lessons[i] for i in unique_indices]
    
    return [
        {
            "lesson_id": a.id, "lesson_value": f"{a.lesson} - otherwise you'll see problems like this: {a.pattern} ({a.trigger})"
        }
        for a in lessons
    ]


def extract_lessons(
        config: CodingAgentConfig,
        agent_step: str,
        log_content,
        skip=False
):
    if skip:
        return
    
    current_iteration = config.current_iteration
    task = config.task
    lessons_data = llm_chat(
        "Extract Lessons",
        [
            get_sys_prompt("lesson_extractor.md"),
            task.get_work_desc(),
            LlmMessage.user_from_data(
                f"Log Content from the '{agent_step}' step",
                log_content
            ),
            get_lessons_msg(
                "Existing Lessons (Don't repeat these)",
                config,
                exclude_invalid=False
            )
        ],
        output_schema="lesson_extractor.md.schema.json",
        tag_entity=config.current_iteration
    ).json()
    
    for lesson_data in common.ensure_list(common.get(lessons_data, "lessons", [])):
        AgentLesson.create_from_data(
            agent_step,
            lesson_data,
            current_iteration
        )


def plan_initiative_tdd_code_changes(config: CodingAgentConfig):
    config.iterate_if_necessary()
    
    task = config.task
    initiative = config.initiative
    
    return plan_tdd_code_changes(
        config,
        user_messages=[
            LlmMessage.sys_from_data(
                """
                    Please create a plan to one-shot write a single file, comprensive test suite that asserts 
                    the following Initiative has been implemented correctly
                    
                    This test must always assert end-to-end behavior using real services (never mocks)
                    This test is the last line of QA before a production push
                """,
                {
                    "name": initiative.title,
                    "description": initiative.description,
                    "user_documentation": initiative.user_documentation or "None"
                }
            )
        ]
    )


def plan_task_tdd_code_changes(config: CodingAgentConfig):
    config.iterate_if_necessary()
    task = config.task
    
    initiative = task.initiative
    goal_data = {
        "GOAL": task.description,
        "acceptance_criteria": task.completion_criteria,
        "risk_notes": task.risk_notes,
    }
    if task.debug_steps:
        goal_data['debug_steps'] = task.debug_steps
    
    # Build test authoring instructions based on phase
    if config.is_ui_first_phase:
        test_instruction = textwrap.dedent("""
            **You Must** create a plan to write a comprehensive test suite that asserts this task's behavior.

            **For UI-First Phase - Multi-File Test Plan**:
            - Create/update the test file in `__tests__/` directory (e.g., `__tests__/ComponentName.test.js`)
            - Create/update `__tests__/setup.js` with mock API responses needed for this test
            - The setup.js file should mock global fetch/axios with predefined responses for API endpoints used by this feature
            - Tests must run standalone without server interaction
            - All API mocks go in setup.js, NOT inline in the test file

            **Important**: Your plan may include changes to BOTH the test file AND setup.js file.
            This is Test Driven Development for UI-first phase.
        """)
    else:
        test_instruction = textwrap.dedent("""
            **You Must** create a plan to one-shot write a **single file**, comprehensive test suite that asserts this task's behavior.
            This test suite will be used for Test Driven Development.
        """)
    
    return plan_tdd_code_changes(
        config,
        user_messages=[
            LlmMessage.user_from_data(
                textwrap.dedent("""
                **Parent Initiative**

                This 'parent initiative' data is for higher level context only.
                - The test you write **only** needs to assert the task's 'GOAL' and 'acceptance_criteria'
                - But, if in writing the test you identify that you need to know more context about the task, look at the parent_initiative data for that higher level context
            """),
                {
                    "parent_initiative_name": initiative.title,
                    "parent_initiative_description": initiative.description,
                    "parent_initiative_user_documentation": initiative.user_documentation or "None"
                }
            ),
            LlmMessage.user(test_instruction),
            LlmMessage.user_from_data(
                "Task to test",
                goal_data
            )
        ]
    )


def get_existing_test_context_messages(
        initiative: Initiative,
        excluding_test_path: Path = None,
        title: str = None
) -> list[LlmMessage]:
    title = title or textwrap.dedent(f"""
        Existing automated tests that must continue to pass and should not be duplicated.
        New tests must complement the provided suites, avoid duplicating coverage,
        and remain consistent so all tests can pass together.
    """)
    
    """Build context messages describing other automated tests to avoid duplication."""
    existing_test_entries: list[dict[str, str]] = []
    
    # Query for both Python tests (.py) and JavaScript/TypeScript tests (.test.js, .test.jsx, .test.ts, .test.tsx)
    from django.db.models import Q
    
    code_version_map = {
        cv.code_file_id: cv
        for cv in CodeVersion.objects.filter(
            task_iteration__self_driving_task__task__initiative_id=initiative.id
        ).filter(
            Q(code_file__file_path__startswith="test", code_file__file_path__endswith=".py") |
            Q(code_file__file_path__contains=".test.js") |
            Q(code_file__file_path__contains=".test.jsx") |
            Q(code_file__file_path__contains=".test.ts") |
            Q(code_file__file_path__contains=".test.tsx") |
            Q(code_file__file_path__contains="__tests__")
        ).order_by("task_iteration__version_number").select_related("code_file")
    }
    
    for cv in code_version_map.values():
        file_path = cv.code_file.file_path
        if excluding_test_path and file_path == str(excluding_test_path):
            continue
        
        existing_test_entries.append({
            "path": file_path,
            "code": cv.code
        })
    
    if not existing_test_entries:
        return []
    
    max_files_to_embed = 8
    selected_entries = existing_test_entries[:max_files_to_embed]
    
    messages = LlmMessage.user_from_data(
        title,
        selected_entries,
        item_name="file"
    )
    
    if len(existing_test_entries) > max_files_to_embed:
        remaining_paths = [entry["path"] for entry in existing_test_entries[max_files_to_embed:]]
        messages.append(
            LlmMessage.user(
                "Additional existing test files (code omitted for brevity):\n" + "\n".join(remaining_paths)
            )
        )
    
    return messages


def plan_tdd_code_changes(
        config: CodingAgentConfig,
        user_messages: list[LlmMessage]
):
    planning_json = plan_iterative_code_changes(
        config,
        goal_instructions=[
            LlmMessage.sys("""
            **Engage Test Authoring Mode**
            
            The plan **must** define instructions for writing a **single test file**
            """),
            get_existing_test_context_messages(config.initiative),
            common.ensure_list(user_messages)
        ],
        include_prev_iterations=False
    )
    
    tdd_test_file = planning_json.get("tdd_test_file")
    if not tdd_test_file:
        raise BadPlan(f"When writing an automated test plan, the planning data **must** include a file path value for `tdd_test_file`")
    
    return planning_json


def update_file_contents(
        config: CodingAgentConfig,
        file_path: Path,
        code: str
):
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(code)
    with transaction.atomic():
        code_verson = config.current_iteration.get_code_version(file_path)
    return file_path


def get_tombstone_message(config: CodingAgentConfig, disabled=True) -> list[LlmMessage]:
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


def get_ui_first_phase_context_msg(config: CodingAgentConfig) -> list[LlmMessage]:
    """
    Returns context message about UI-first phase and frontend framework when applicable.

    This message informs the planner that:
    1. This is UI-first phase (UI + Mock API, no AWS deployment)
    2. What frontend framework is being used (React, React Native)
    3. That tests should be Jest-based frontend tests, not Django backend tests
    """
    if not config.is_ui_first_phase:
        return []
    
    frontend_info = detect_frontend_framework(config)
    
    if not frontend_info["has_frontend"]:
        # UI-first phase but no frontend detected yet - provide guidance
        return [LlmMessage.user(textwrap.dedent("""
            ## UI-First Phase Context

            This task is in **UI-First Phase** (UI + Mock API implementation, no AWS deployment).

            When writing tests for UI-first phase:
            - Tests should be Jest-based frontend tests (React Testing Library or React Native Testing Library)
            - Tests should NOT be Django backend tests
            - The `tdd_test_file` should be a JavaScript/TypeScript test file (e.g., `.test.js`, `.test.jsx`, `.test.ts`, `.test.tsx`)
            - Tests should be placed in a `__tests__/` directory
            - **CRITICAL**: Tests must run standalone without server interaction
            - **Mock API Setup**: Create `__tests__/setup.js` file to configure all API mocks
            - All mock API calls and responses MUST be defined in `setup.js`, not inline in test files
            - Tests should validate UI behavior using the mocked API responses
        """))]
    
    framework = frontend_info["framework"]
    framework_name = "React Native" if framework == "react-native" else "React Web"
    frontend_dir = frontend_info["frontend_dir"]
    
    # Compute relative path from sandbox root to frontend directory
    frontend_rel_path = frontend_dir.relative_to(config.sandbox_root_dir)
    tests_dir_path = frontend_rel_path / "__tests__"
    
    test_framework_guidance = ""
    if framework == "react-native":
        test_framework_guidance = textwrap.dedent(f"""
            - Use Jest with React Native Testing Library
            - Test file should use `.test.js` or `.test.tsx` extension
            - Place test file in: `{tests_dir_path}/` directory
            - Example path: `{tests_dir_path}/ComponentName.test.js`
            - Tests should validate React Native component behavior
            - **CRITICAL**: Tests must run standalone without server interaction
            - **Mock API Setup**: Create `{tests_dir_path}/setup.js` file to configure all API mocks
            - All mock API calls and responses MUST be defined in `{tests_dir_path}/setup.js`, not inline in test files
            - Example: Mock global fetch in setup.js with predefined responses for all endpoints
        """)
    elif framework == "react":
        test_framework_guidance = textwrap.dedent(f"""
            - Use Jest with React Testing Library
            - Test file should use `.test.js` or `.test.tsx` extension
            - Place test file in: `{tests_dir_path}/` directory
            - Example path: `{tests_dir_path}/ComponentName.test.js`
            - Tests should validate React component rendering and user interactions
            - **CRITICAL**: Tests must run standalone without server interaction
            - **Mock API Setup**: Create `{tests_dir_path}/setup.js` file to configure all API mocks
            - All mock API calls and responses MUST be defined in `{tests_dir_path}/setup.js`, not inline in test files
            - Example: Mock global fetch in setup.js with predefined responses for all endpoints
        """)
    
    return [LlmMessage.user(textwrap.dedent(f"""
        ## UI-First Phase Context

        This task is in **UI-First Phase** (UI + Mock API implementation, no AWS deployment).

        **Detected Frontend Framework**: {framework_name}
        **Frontend Directory**: {frontend_rel_path}/

        **Test Requirements for UI-First Phase**:
        {test_framework_guidance}

        **Important**: Do NOT generate Django/Python backend tests. This is UI-first phase - tests must be frontend tests.
    """))]


def get_readonly_files_replacement(config: CodingAgentConfig) -> tuple[str, str]:
    parts = []
    
    for f in config.self_driving_task.get_readonly_files():
        if f['alternatives']:
            parts.append(f"- `{f['path']}` — {f['description']}. If you believe a change is needed to {f['path']}, the change likely belongs in `{f['alternatives']}` instead")
        else:
            parts.append(f"- `{f['path']}` — {f['description']}")
    
    return "<read_only_files>", "\n".join(parts)


def plan_iterative_code_changes(config: CodingAgentConfig, goal_instructions=None, include_prev_iterations=False):
    current_iteration = config.current_iteration
    previous_iteration = config.previous_iteration
    iteration_to_modify = config.iteration_to_modify
    
    business = config.self_driving_task.business
    
    task = config.self_driving_task.task
    task_type = TaskType(task.task_type)
    
    messages = [
        get_sys_prompt(
            "codeplanner--codewriter_audience.md",
            replacements=[
                ("<credential_manager_existing_services>", credential_manager.get_existing_service_names_desc()),
            ]
        ),
        get_architecture_docs(
            config.initiative
        ),
        config.business.get_existing_required_credentials_llmm(),
        get_budget_message(
            config
        ),
        build_opentofu_plan_context_messages(
            config
        ),
        get_tombstone_message(
            config
        ),
        build_previous_iteration_context_messages(
            config
        ) if include_prev_iterations else [],
        get_dependencies_msg(
            config,
            for_planning=True
        ),
        get_file_structure_msg(
            config.sandbox_root_dir
        ) if not iteration_to_modify.has_error() else [],
        get_guidance_msg(
            config
        ),
        get_ui_first_phase_context_msg(
            config
        ),
        get_lessons_msg(
            "Do not repeat these mistakes - before you respond, checklist each item to make sure you're not repeating it",
            config
        ),
        LlmMessage.user_from_data(
            "Strategic Unblocking Guidance",
            config.iteration_to_modify.strategic_unblocking_json
        ) if config.iteration_to_modify.strategic_unblocking_json else None,
        get_goal_msg(config, "Please plan code changes that work towards achieving this GOAL") if not goal_instructions else common.ensure_list(goal_instructions)
    ]
    
    planning_data = config.frontier_session.plan_changes(messages)
    planning_model_value = planning_data.pop("_llm_model", LlmModel.OPENAI_GPT_5_MINI.value)

    with transaction.atomic():
        SelfDrivingTaskIteration.objects.filter(id=current_iteration.id).update(
            planning_model=planning_model_value,
            planning_json=planning_data,
            execute_module=planning_data.get('execute_module'),
            test_module=planning_data.get('test_module')
        )
        current_iteration.refresh_from_db(fields=["planning_model", "execute_module", "test_module"])
    
    blocked_data = planning_data.get('blocked')
    if blocked_data:
        raise AgentBlocked(blocked_data)
    
    return planning_data


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
        [
            LlmMessage.sys(
                "You are a code fixer. Your job is to fix syntax or compilation errors in a code file."
            ),
            LlmMessage.user(
                f"""This is the code (from file {code_file_path}) that failed validation:
                ```
                {code}
                ```

                Here is the exception message:
                {str(e)}

                Please return only the corrected version of the code. No explanation, no formatting."""
            )
        ],
        model=LlmModel.OPENAI_GPT_5_MINI,
        tag_entity=config.current_iteration,
        code_response=True
    ).text
    return code


def get_codewriter_system_prompt(code_file_path) -> list[str]:
    code_file_path_str = str(code_file_path).lower()
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
        if 'lambda' in code_file_path_str:
            prompt = [
                "codewriter--python_coder.md",
                "codewriter--lambda_coder.md",
                "common--llm_chat.md"
            ]
        else:
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
    elif code_file_name == InfrastructureStackType.APPLICATION.get_opentofu_config():
        prompt = [
            "codewriter--aws_cloudformation_coder_tofu.md",
            "common--infrastructure_rules_tofu.md"
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
        raise AgentBlocked(f"no coder implemented for {code_file_name}.  Need a human to implement it in the Erie Iron agent codebase.")
    
    return common.ensure_list(prompt)


def get_dependencies_msg(config: CodingAgentConfig, for_planning: bool) -> list[LlmMessage]:
    header = "The python environment has the following packages installed"
    if for_planning:
        header += ".  If you need additional packages, you'll need to add them to the requirements.txt"
    
    return get_requirementstxt_msg(
        (config.sandbox_root_dir / 'requirements.txt').read_text(),
        header
    )


def get_docs_msg(config) -> list[LlmMessage]:
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


def get_goal_msg(config, description):
    task = config.self_driving_task.task
    
    d = {
        "PRIMARY_OBJECTIVE": "achieving the `GOAL` is the **primary objective** of this code iteration",
        "previous_iteration_errors": common.get(config.iteration_to_modify, "exceptions", default_val="none"),
        "domain_name": textwrap.dedent(f"""
                Note:  the domain name is dynamic and may change between test runs.  
                - the current domain name is `{config.initiative.domain}`
                - previous test failures might reference a different domain. As the domain name is dynamic, this is expected and not a cause for concern
            """)
    }
    
    test_errors = config.iteration_to_modify.get_unit_test_errors() if config.iteration_to_modify else []
    
    if not common.get(config.iteration_to_modify, ["log_content_deployment", "deployment_successful"]):
        goal = textwrap.dedent(f"""
            The previous iteration failed at the OpenTofu deployment stage.   

            **Application level code changes are FORBIDDEN at this point, and will be FORBIDDEN until the deployment is fixed**
            - Any application level code changes at this point would be purely speculative and not based on an execution feedback loop
            - You may only plan changes for environment / infrastructure files (Dockerfile, OpenTofu modules under `opentofu/`, requirements.txt, etc.)

            **YOUR GOAL IS TO FIX THE DEPLOYMENT PROBLEM** 
        """)
    elif test_errors:
        goal = textwrap.dedent(f"""
            One or more of the tests in support of {task.description} are failing.  
            
            Your GOAL is to **MAKE THE FAILING TESTS PASS**
        """)
        
        if test_errors:
            d["failing_tests"] = test_errors
    else:
        goal = textwrap.dedent(f"""
            ## YOUR GOAL
            Write code to implement and/or deploy this task:
            '''
            {task.description}
            '''

            ## Acceptance Criteria
            {task.completion_criteria}

            ## Risk Notes (FYI)
            {task.risk_notes}
        """)
    
    if task.debug_steps:
        goal += f"""

        ## Manual Debugging Steps (FYI)
        {task.debug_steps}
        """
    
    return LlmMessage.user_from_data(description, {
        "GOAL": goal,
        **d
    })


def sync_stack_identity(
        config: CodingAgentConfig,
        container_env: dict,
        cloudformation_params: dict = None
):
    stack_application = config.stack
    
    container_env["STACK_NAME"] = stack_application.stack_name
    container_env["STACK_IDENTIFIER"] = container_env["TASK_NAMESPACE"] = stack_application.stack_namespace_token
    
    if cloudformation_params:
        cloudformation_params["StackIdentifier"] = stack_application.stack_namespace_token


def deploy_stack(
        config: CodingAgentConfig,
        *,
        container_image_tag: str = None,
        lambda_datas: list | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    config.set_phase(SdaPhase.DEPLOY)
    
    stack = config.stack
    stack_manager = config.stack_manager
    stack_manager.validate_stack()
    
    if not container_image_tag:
        container_image_tag = common.assert_not_empty(config.task.get_container_image_tag())
    
    web_container_image = config.aws_interface.get_full_image_uri(
        config.ecr_repo_name,
        container_image_tag,
        config.env_type.get_aws_region()
    )
    
    ecr_arn = config.aws_interface.get_ecr_arn(
        config.ecr_repo_name,
        container_image_tag,
        config.env_type.get_aws_region()
    )
    
    tfvars_payload = build_tfvars_payload(
        config,
        stack,
        web_container_image=web_container_image,
        ecr_arn=ecr_arn,
        lambda_datas=lambda_datas
    )

    config.frontier_session.patch_iac({
        "operation": "deploy_stack",
        "stack_name": stack.stack_name,
        "env": config.env_type.value,
        "tfvars": tfvars_payload,
        "lambda_count": len(lambda_datas or []),
    })

    config.stack_manager.set_stack_vars(tfvars_payload)
    
    error_payload = None
    try:
        # Construct the correct composite security group rule import ID
        
        stack_manager.import_external_resources([
            ("aws_security_group_rule.rds_ingress_client", config.aws_interface.get_rds_ingress_securitygroup_rule_id()),
            ("aws_security_group_rule.rds_ingress_vpc", config.aws_interface.get_rds_ingress_vpc_rule_id())
        ])
        
        config.log(f"applying tofu as {stack_manager.cloud_account.get_service_client('sts').get_caller_identity()}")
        stack_manager.plan()
        stack_manager.apply()
        ingest_tofu_ouputs(config)
        
        if stack_manager.get_resource_definitions("aws_ses_domain_dkim"):
            ses_manager.manage_ses_domain_settings(
                config.cloud_account,
                str(tfvars_payload.get("DomainName") or "").strip()
            )
        
        stack.resources = stack_manager.get_resources()
        stack.save()
        
        ensure_lb_alias_record(config)
    except OpenTofuCommandException as open_tofu_exception:
        open_tofu_exception.log_error()
        
        error_payload = open_tofu_exception.extract_error_payload()
        
        stack_manager.record(
            stack_manager.stage,
            open_tofu_exception.result
        )
        
        raise open_tofu_exception
    finally:
        config.add_deployment_log(
            config.stack_manager.get_deployment_logs(error_payload)
        )


def build_tfvars_payload(
        config: CodingAgentConfig,
        stack: InfrastructureStack,
        *,
        web_container_image: str | None,
        ecr_arn: str | None,
        lambda_datas: list | None
) -> dict[str, Any]:
    stack_variables = config.stack_manager.get_stack_variables()
    
    forbidden_variables = {"DBName", "DBPassword"}
    forbidden_in_module = forbidden_variables.intersection(stack_variables.keys())
    if forbidden_in_module:
        raise BadPlan(
            json.dumps({
                "description": "Terraform module defines forbidden variables",
                "forbidden_variables": sorted(forbidden_in_module),
                "hint": "Remove these variable declarations. Database credentials are sourced from Secrets Manager."
            }, indent=4),
            config.current_iteration.planning_json
        )
    
    env_type = config.env_type
    business = config.initiative.business
    secrets_key = business.get_secrets_root_key(env_type)
    
    developer_cidr = common.get_ip_address()
    shared_vpc = config.aws_interface.get_shared_vpc()
    
    payload: dict[str, Any] = {
        "StackIdentifier": config.stack.stack_namespace_token,
        "ClientIpForRemoteAccess": developer_cidr,
        "ErieIronEnv": config.env_type.value,
        "DeletePolicy": "Retain" if EnvironmentType.PRODUCTION.eq(env_type) else "Delete",
        "AWS_ACCOUNT_ID": settings.AWS_ACCOUNT_ID,
        **get_admin_credentials(
            config,
            secrets_key
        )
    }
    
    if ecr_arn:
        payload["ECRRepositoryArn"] = ecr_arn
    
    if web_container_image:
        payload["WebContainerImage"] = web_container_image
    
    if business.web_container_cpu is not None:
        payload["WebContainerCpu"] = business.web_container_cpu
    if business.web_container_memory is not None:
        payload["WebContainerMemory"] = business.web_container_memory
    if business.web_desired_count is not None:
        payload["WebDesiredCount"] = business.web_desired_count
    
    missing_secret_params = []
    for credential_service, secret_arn in config.stack.get_credential_arns(CredentialServiceProvisioning.USER_SUPPLIED):
        if not secret_arn:
            missing_secret_params.append(credential_service)
        else:
            stack_var, _ = credential_service.get_stackoutput_and_env_names()
            payload[stack_var] = secret_arn
    
    if missing_secret_params:
        raise AgentBlocked({
            "description": "Missing required secret ARN variables for Terraform module",
            "missing_secret_variables": sorted(missing_secret_params),
            "hint": "Need to manually manage the credentialy in the Erie Iron UI"
        })
    
    if CredentialService.COGNITO.value in business.required_credentials:
        payload["EnableCognito"] = True
        mobile_scheme = getattr(business, "mobile_app_scheme", None) or business.service_token
        if mobile_scheme:
            payload["MobileAppScheme"] = mobile_scheme
    
    domain_name = business.domain if EnvironmentType.PRODUCTION.eq(config.env_type) else config.initiative.domain
    if not domain_name:
        raise AgentBlocked(
            json.dumps({
                "description": "Initiative has no domain",
                "stack_id": config.stack.id
            }, indent=4)
        )
    
    payload["DomainName"] = domain_name
    payload["DomainHostedZoneId"] = config.domain_manager.get_hosted_zone_id_by_domain(
        business.domain
    )
    payload["AlbCertificateArn"] = config.domain_manager.find_certificate_arn(
        business.domain,
        config.env_type.get_aws_region()
    )
    
    payload["VpcId"] = shared_vpc.vpc_id
    if shared_vpc.cidr_block:
        payload.setdefault("VpcCidr", shared_vpc.cidr_block)

    if shared_vpc.public_subnet_ids:
        payload["PublicSubnet1Id"] = shared_vpc.public_subnet_ids[0]
        payload["PublicSubnet2Id"] = shared_vpc.public_subnet_ids[1] if len(shared_vpc.public_subnet_ids) >= 2 else shared_vpc.public_subnet_ids[0]

    if shared_vpc.private_subnet_ids:
        payload["PrivateSubnet1Id"] = shared_vpc.private_subnet_ids[0]
        payload["PrivateSubnet2Id"] = shared_vpc.private_subnet_ids[1] if len(shared_vpc.private_subnet_ids) >= 2 else shared_vpc.private_subnet_ids[0]

    payload["SecurityGroupId"] = shared_vpc.rds_security_group_id
    payload["CreateIngressRule"] = True  # Explicitly ensure ALB can reach ECS tasks
    
    for lambda_data in lambda_datas or []:
        param_name = lambda_data.get('s3_key_param')
        param_value = lambda_data.get('s3_key_name')
        if param_name and param_value:
            payload[param_name] = param_value
    
    provided_values = {
        key: value
        for key, value in payload.items()
        if value not in [None, ""]
    }
    
    module_variable_names = set(stack_variables.keys())
    if module_variable_names:
        missing_variables = StackManager.validate_required_variables(
            stack_variables,
            provided_values
        )
        if missing_variables:
            raise BadPlan(json.dumps({
                "description": "Terraform module declares required variables without defaults, but the agent has no value for them",
                "missing_variables": sorted(missing_variables)
            }, indent=4), config.current_iteration.planning_json)
    
    return provided_values


def get_admin_credentials(
        config: CodingAgentConfig,
        secrets_key
):
    aws_secrets_client = config.aws_interface.client("secretsmanager")
    admin_secrets_key = f"{secrets_key}/appadmin"
    
    try:
        secret_value_response = aws_secrets_client.get_secret_value(SecretId=admin_secrets_key)
        creds = json.loads(secret_value_response['SecretString'])
    except aws_secrets_client.exceptions.ResourceNotFoundException:
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


def ecr_authenticate_for_base_image(config: CodingAgentConfig, dockerfile):
    with open(dockerfile) as f:
        # noinspection RegExpSimplifiable
        images = re.findall(
            r'FROM(?:\s+--platform=\$\w+)?\s+(\d+\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[^\s:]+:[^\s]+)',
            f.read(),
            flags=re.IGNORECASE
        )
    
    for base_img in images:
        last_err = None
        for attempt in range(3):
            try:
                ecr_login(config, base_img, CredentialsSpace.ERIE_IRON)
                last_err = None
                break
            except Exception as e:
                last_err = e
                config.log(f"ECR login failed for {base_img} on attempt {attempt + 1}: {e}")
        
        if last_err:
            raise AgentBlocked({
                "desc": f"Unable to authenticate with ECR for {base_img} after 3 attempts",
                "error": str(last_err)
            })


def ecr_login(config: CodingAgentConfig, ecr_repo_uri, credentials_space):
    """
    Log into the correct ECR registry for the provided image URI.
    Ensures the registry hostname is parsed correctly (account.dkr.ecr.region.amazonaws.com).
    """
    try:
        # Extract registry hostname (everything before the first slash)
        registry = ecr_repo_uri.split("/", 1)[0].strip()
        if not registry:
            raise ValueError(f"Unable to extract registry from ECR URI: {ecr_repo_uri}")
        
        # Extract region from registry string
        region = parse_region_from_ecr_uri(ecr_repo_uri)
        
        subprocess.run(
            (
                f"aws ecr get-login-password --region {region} "
                f"| podman login --username AWS --password-stdin {registry}"
            ),
            shell=True,
            check=True,
            env=config.get_env_for_credentials_space(credentials_space),
            stdout=config.log_f,
            stderr=subprocess.STDOUT
        )
    except Exception as e:
        config.log(f"ECR login failed for {ecr_repo_uri}: {e}")
        raise


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


def empty_stack_buckets(
        config: CodingAgentConfig, *,
        delete_bucket=True
):
    if not EnvironmentType.DEV.eq(config.env_type):
        return
    
    bucket_definitions = get_stack_buckets(config)
    if not bucket_definitions:
        return
    
    logging.info(f"Found {len(bucket_definitions)} S3 bucket(s) in stack, emptying before deletion...")
    
    for bucket_resource in bucket_definitions:
        config.aws_interface.empty_s3_bucket(bucket_resource["bucket_name"], delete_bucket=delete_bucket)
