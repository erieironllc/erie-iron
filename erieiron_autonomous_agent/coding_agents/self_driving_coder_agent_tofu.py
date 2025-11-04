import copy
import subprocess
import json
import logging
import os
import re
import textwrap
import time
import traceback
import shutil
from collections import defaultdict
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from typing import Any, Mapping, Optional

import boto3
import botocore.session
import yaml
from django.db import transaction
from django.db.models import Func
from django.db.models import Q
from django.db.models.expressions import RawSQL
from erieiron_public import agent_tools

import settings
from erieiron_autonomous_agent.coding_agents import credential_manager
from erieiron_autonomous_agent.coding_agents.self_driving_coder_config import (
    TASK_DESC_CODE_WRITING,
    MAP_TASKTYPE_TO_PLANNING_PROMPT,
    SdaPhase,
    LAMBDA_PACKAGES_BUCKET,
    ERIEIRON_PUBLIC_COMMON_VERSION,
    USE_CODEX,
    SdaInitialAction,
    SelfDriverConfig,
)
from erieiron_autonomous_agent.coding_agents.self_driving_coder_exceptions import AgentBlocked, NeedPlan, RetryableException, BadPlan, GoalAchieved, CodeReviewException, ExecutionException, FailingTestException, DatabaseMigrationException
from erieiron_autonomous_agent.enums import TaskStatus
from erieiron_autonomous_agent.models import (
    CodeVersion,
    CodeMethod,
    SelfDrivingTaskIteration,
    Task,
    SelfDrivingTask,
    CodeFile,
    AgentLesson,
    AgentTombstone,
    LlmRequest,
    Business,
    InfrastructureStack, Initiative,
)
from erieiron_autonomous_agent.system_agent_llm_interface import llm_chat, get_sys_prompt
from erieiron_autonomous_agent.utils import codegen_utils
from erieiron_autonomous_agent.utils.codegen_utils import CodeCompilationError, get_codebert_embedding, validate_dockerfile
from erieiron_common import common, aws_utils, domain_manager, opentofu_log_utils, aws_log_reader
from erieiron_common.aws_utils import sanitize_aws_name, empty_s3_bucket, package_lambda, get_full_image_uri
from erieiron_common.chat_engine.language_utils import get_text_embedding
from erieiron_common.enums import LlmModel, PubSubMessageType, TaskType, TaskExecutionSchedule, EnvironmentType, DevelopmentRoutingPath, LlmReasoningEffort, CredentialService, LlmVerbosity, LlmMessageType, ContainerPlatform, InfrastructureStackType, BuildStep
from erieiron_common.llm_apis.llm_constants import MODEL_PRICE_USD_PER_MILLION_TOKENS
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_common.message_queue.pubsub_manager import PubSubManager
from erieiron_common.opentofu_helpers import OpenTofuException, OpenTofuCommandError
from erieiron_common.opentofu_stack_manager import OpenTofuStackManager


MIN_PODMAN_STORAGE_FREE_GB = 4.0


def execute(
        task_id: str,
        one_off_action: SdaInitialAction = None
):
    try:
        self_driving_task = bootstrap_selfdriving_agent(task_id)
        
        if one_off_action:
            with SelfDriverConfig(self_driving_task, one_off_action) as config:
                execute_one_off_action(config, one_off_action)
            return
    except AgentBlocked as agent_blocked:
        logging.info(agent_blocked)
        handle_agent_blocked(task_id, agent_blocked)
        logging.info(f"Stopping - Agent Blocked")
        return
    
    for i in range(20):
        self_driving_task.get_git().pull()
        
        with SelfDriverConfig(self_driving_task) as config:
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
                    
                    if config.iteration_to_modify and i > 0:
                        config.iteration_to_modify.write_to_disk()
                    
                    plan_and_implement_code_changes(config)
                
                build_deploy_exec_iteration(
                    config
                )
                
                evaluate_iteration(
                    config
                )
            
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
                config.log(agent_blocked)
                handle_agent_blocked(task_id, agent_blocked)
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


def bootstrap_first_cycle(config: SelfDriverConfig, self_driving_task: SelfDrivingTask):
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


def execute_one_off_action(config: SelfDriverConfig, one_off_action: SdaInitialAction):
    config.init_log()
    
    print(textwrap.dedent(f"""
            
            
            Running {one_off_action} for
            {config.task.id}
            {config.task.description}
            
            
            """))
    
    config.set_iteration(
        SelfDrivingTaskIteration.objects.filter(planning_json__isnull=False).order_by("-timestamp").first()
    )
    
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
    
    if SdaInitialAction.WRITE_INITIATIVE_TEST.eq(one_off_action):
        write_initiative_tdd_test(config)
    elif SdaInitialAction.CODE.eq(one_off_action):
        codex_exec(
            config,
            config.current_iteration.planning_json
        )
    elif SdaInitialAction.PLAN.eq(one_off_action):
        # config.iteration_to_modify.strategic_unblocking_json = get_strategic_unblocking_data(config)
        # config.iteration_to_modify.save()
        plan_and_implement_code_changes(
            config
        )
    
    if not SdaInitialAction.EVAL.eq(one_off_action):
        build_deploy_exec_iteration(
            config
        )
    
    evaluate_iteration(config)


def plan_and_implement_code_changes(config):
    if config.self_driving_task.initial_tests_pass and not config.self_driving_task.test_file_path:
        config.set_phase(SdaPhase.CODING)
        
        if TaskType.INITIATIVE_VERIFICATION.eq(config.task_type):
            write_initiative_tdd_test(config)
        else:
            write_task_tdd_test(config)
    
    else:
        planning_data = plan_code_changes(config)
        config.set_phase(SdaPhase.CODING)
        
        if USE_CODEX:
            codex_exec(config, planning_data)
        else:
            do_coding(config, planning_data)


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
    if TaskType.CODING_ML.eq(config.task_type):
        from erieiron_autonomous_agent.coding_agents.ml_packager import package_ml_artifacts
        package_ml_artifacts(config)
    
    try:
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


def do_coding(config, planning_data):
    config.current_iteration.codeversion_set.all().delete()
    
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
    if config.task_type.eq(TaskType.PRODUCTION_DEPLOYMENT):
        config.log("Skipping test regeneration for production deployment task")
        return config
    
    if TaskType.INITIATIVE_VERIFICATION.eq(config.task_type):
        write_initiative_tdd_test(config)
    else:
        write_task_tdd_test(config)
    
    return config


def codex_exec(config: SelfDriverConfig, planning_data: dict):
    config.current_iteration.codeversion_set.all().delete()
    
    """Execute the plan using the Codex CLI pipeline."""
    config.log("Starting Codex CLI planning/execution pipeline")
    
    plan_path = config.artifacts_dir / f"{config.current_iteration.id}_plan.json"
    prompt_path = config.artifacts_dir / f"{config.current_iteration.id}_codex_prompt.txt"
    stdout_path = config.artifacts_dir / f"{config.current_iteration.id}_codex_stdout.log"
    stderr_path = config.artifacts_dir / f"{config.current_iteration.id}_codex_stderr.log"
    last_message_path = config.artifacts_dir / f"{config.current_iteration.id}_codex_last_message.txt"
    
    try:
        
        iteration_to_modify = config.iteration_to_modify
        
        readonly_entries = config.self_driving_task.get_readonly_files()
        readonly_lines = []
        for entry in readonly_entries:
            if not entry:
                continue
            description_parts = [entry.get("description")]
            if entry.get("alternatives"):
                description_parts.append(
                    f"If a change is required, route it to {entry['alternatives']} instead."
                )
            description_text = "; ".join([p for p in description_parts if p])
            readonly_lines.append(
                f"- {entry.get('path')}: {description_text or 'This path is read-only.'}"
            )
        
        code_file_entries = common.ensure_list(planning_data.get("code_files"))
        code_file_paths = [
            entry.get("code_file_path")
            for entry in code_file_entries
            if isinstance(entry, dict) and entry.get("code_file_path")
        ]
        code_file_summary_lines = [f"- {path}" for path in code_file_paths]
        
        business = config.business
        initiative = config.initiative
        task = config.task
        
        plan_path.write_text(json.dumps(planning_data, indent=2, default=str), encoding="utf-8")
        
        reference_prompts = [
            "prompts/common--general_coding_rules.md",
            "prompts/common--agent_provided_functionality_tofu.md",
            "prompts/common--infrastructure_rules_tofu.md",
            "prompts/common--credentials_architecture_tofu.md",
            "prompts/codewriter--common.md",
            "prompts/codewriter--python_coder.md",
            "prompts/codewriter--lambda_coder.md",
            "prompts/codewriter--aws_cloudformation_coder_tofu.md",
            "prompts/codewriter--requirements.txt.md",
        ]
        
        prompt_parts = [
            textwrap.dedent(f"""
            You are the Codex CLI agent assisting Erie Iron's self-driving coding workflow.
            Operate strictly within the sandboxed repository at {config.sandbox_root_dir}.
            Follow the approved development plan saved at {plan_path} and summarised below.
            Before editing a file, consult the relevant engineering standards from the prompts
            directory (see the Reference Prompts section).
            Do not commit or push changes; the orchestrator handles git commits.
            """),
            textwrap.dedent(f"""
            ### Risk Notes
            {task.risk_notes or 'None provided.'}
            """),
            textwrap.dedent(f"""
            ## Business & Architecture Context
            Business Service Token: {business.service_token}
            Initiative ID: {initiative.id}
            """),
        ]
        
        if initiative.architecture:
            prompt_parts.append(textwrap.dedent(f"""
            ### Initiative Architecture
            {initiative.architecture}
            """))
        
        if initiative.user_documentation:
            prompt_parts.append(textwrap.dedent(f"""
            ### User Documentation
            {initiative.user_documentation}
            """))
        
        prompt_parts.append(textwrap.dedent(f"""
        ### Lessons Learned - do not repeat these errors 
        {json.dumps(get_lessons(config, TASK_DESC_CODE_WRITING), indent=4)}
        """))
        
        if config.guidance:
            prompt_parts.append(textwrap.dedent(f"""
            ## Important Additional Guidance
            {config.guidance}
            """))
        
        if readonly_lines:
            prompt_parts.append(textwrap.dedent("""
            ## Read-only Paths
            These paths must **never** be modified 
            """ + "\n".join(readonly_lines)))
        
        if code_file_summary_lines:
            prompt_parts.append(textwrap.dedent("""
            ## Files Highlighted by the Plan
            Review the instructions for each file in the plan JSON and modify only what is necessary.
            """ + "\n".join(code_file_summary_lines)))
        
        for path in reference_prompts:
            prompt_parts.append(textwrap.dedent(f"""
            
            {Path(path).read_text()}
            """))
        
        guardrail_marker = "Route53 Root Alias Guardrail"
        if not any(guardrail_marker in part for part in prompt_parts):
            prompt_parts.append(textwrap.dedent("""

            ### Route53 Root Alias Guardrail
            - Domain DNS must be published with Route53 `AWS::Route53::RecordSet` alias records. Create `Type: A` (and `AAAA` when IPv6 is required) entries that target the Application Load Balancer via `AliasTarget.DNSName` and `AliasTarget.HostedZoneId`.
            - Do **not** create a `CNAME` for `!Ref DomainName`, even when it contains subdomains; apex-style aliases keep Route53 compliant with DNS standards.
            - Continue using CNAMEs only for tokenized SES sub-records such as DKIM keys.
            """))
        
        prompt_parts.append(textwrap.dedent(f"""

        ## Execution Checklist
        1. Read the full development plan at {plan_path}.
        2. Adhere to all Erie Iron prompts listed above; load additional file-specific prompts (e.g. YAML, Python, SQL) as needed.
        3. Implement code changes that satisfy the plan and address prior failures. Keep modifications scoped to the planned files unless you uncover a necessary dependency.
        4. No read-only files modified
        5. Leave the repository with changes ready for review; do not commit.
        """))
        
        prompt_text = "\n\n".join(part.strip() for part in prompt_parts if part)
        
        prompt_path.write_text(prompt_text, encoding="utf-8")
        
        # Persist Codex-specific metadata onto the planning JSON for future debugging.
        augmented_plan = copy.deepcopy(planning_data)
        augmented_plan["codex_metadata"] = {
            "plan_path": str(plan_path),
            "prompt_path": str(prompt_path),
            "code_file_paths": code_file_paths,
        }
        with transaction.atomic():
            SelfDrivingTaskIteration.objects.filter(id=config.current_iteration.id).update(
                planning_json=augmented_plan
            )
        config.current_iteration.refresh_from_db(fields=["planning_json"])
        
        codex_cmd = [
            "codex",
            "exec",
            "--full-auto",
            "--json",
            "--cd",
            str(config.sandbox_root_dir),
            "--output-last-message",
            str(last_message_path),
            "-"
        ]
        
        prior_file_checksum_map = get_file_checksum_map(config.sandbox_root_dir)
        feedback_sections: list[str] = []
        max_validation_attempts = 2
        attempt = 0
        changed_paths: list[Path] = []
        codex_result = None
        
        while attempt < max_validation_attempts:
            attempt += 1
            prompt_with_feedback = prompt_text
            if feedback_sections:
                prompt_with_feedback = prompt_with_feedback + "\n\n" + "\n\n".join(feedback_sections)
            prompt_path.write_text(prompt_with_feedback, encoding="utf-8")
            
            config.log(
                f"Running Codex CLI (attempt {attempt})",
                codex_cmd,
                f"Prompt saved to {prompt_path}"
            )
            
            codex_start_time = time.time()
            codex_result = subprocess.run(
                codex_cmd,
                input=prompt_with_feedback,
                text=True,
                capture_output=True,
                cwd=str(config.sandbox_root_dir),
                env=os.environ.copy()
            )
            
            stdout_path.write_text(codex_result.stdout or "", encoding="utf-8")
            stderr_path.write_text(codex_result.stderr or "", encoding="utf-8")
            
            # Update the planning JSON with execution artefacts regardless of success so failures retain context.
            planning_record = copy.deepcopy(config.current_iteration.planning_json or augmented_plan)
            codex_metadata = planning_record.get("codex_metadata", {})
            usage_metrics = _extract_codex_usage(
                codex_result.stdout,
                codex_result.stderr,
                last_message_path,
                config
            )
            
            total_cost_usd = 0
            total_tokens = 0
            if usage_metrics:
                codex_metadata.update(usage_metrics)
            
            LlmRequest.objects.create(
                title="Codex",
                reasoning_effort=LlmReasoningEffort.MEDIUM,
                verbosity=LlmVerbosity.LOW,
                business=business,
                initiative=initiative,
                task_iteration=config.current_iteration,
                llm_model=LlmModel.OPENAI_GPT_5,
                token_count=total_tokens,
                price=total_cost_usd,
                response=codex_result.stdout,
                input_messages=[
                    {
                        "role": LlmMessageType.SYSTEM,
                        "content": prompt_with_feedback
                    },
                    {
                        "role": LlmMessageType.USER,
                        "content": json.dumps(planning_data, indent=4)
                    }
                ]
            )
            
            codex_metadata.update({
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                "last_message_path": str(last_message_path) if last_message_path.exists() else None,
                "codex_start_time": codex_start_time,
                "return_code": codex_result.returncode,
                "execution_completed_at": time.time(),
                "attempt": attempt
            })
            if feedback_sections:
                codex_metadata["opentofu_feedback"] = feedback_sections.copy()
            planning_record["codex_metadata"] = codex_metadata
            with transaction.atomic():
                SelfDrivingTaskIteration.objects.filter(id=config.current_iteration.id).update(
                    planning_json=planning_record
                )
            config.current_iteration.refresh_from_db(fields=["planning_json"])
            
            if codex_result.returncode != 0:
                raise ExecutionException(
                    f"Codex CLI exited with code {codex_result.returncode}. See {stdout_path} and {stderr_path} for details."
                )
            
            if last_message_path.exists():
                config.log(
                    f"Codex CLI final message (attempt {attempt}):",
                    last_message_path.read_text(encoding="utf-8")
                )
            
            config.log(
                "Codex CLI completed successfully",
                {
                    "stdout_path": str(stdout_path),
                    "stderr_path": str(stderr_path),
                    "last_message_path": str(last_message_path),
                    "attempt": attempt
                }
            )
            
            changed_paths = _collect_repo_changed_files(
                config,
                prior_file_checksum_map,
                readonly_entries
            )
            
            if not changed_paths:
                raise BadPlan("Codex CLI produced no persistable file changes")
            
            normalized_changed = {_normalize_relative_path(p) for p in changed_paths}
            
            validation_error = validate_all_changed_files(
                config,
                normalized_changed,
                planning_data
            )
            
            if validation_error is None:
                break
            
            if attempt >= max_validation_attempts:
                raise validation_error
            
            extract_lessons(
                config,
                TASK_DESC_CODE_WRITING,
                validation_error
            )
            
            feedback_sections.append(
                textwrap.dedent(
                    f"""
                    Code validaton failed with the following error:
                    {validation_error}

                    Apply the error details above to correct the problem
                    """
                ).strip()
            )
            config.log(
                "OpenTofu validation failed after Codex execution; retrying Codex with feedback",
                str(validation_error)
            )
        else:
            raise ExecutionException(
                "Codex CLI reached maximum validation attempts without resolving OpenTofu validation errors."
            )
        
        persisted_code_files = _persist_codex_code_versions(
            config,
            changed_paths,
            planning_data
        )
        
        planning_record = copy.deepcopy(config.current_iteration.planning_json or {})
        if not isinstance(planning_record, dict):
            planning_record = {}
        
        codex_metadata = planning_record.get("codex_metadata", {})
        codex_metadata["persisted_code_files"] = persisted_code_files
        planning_record["codex_metadata"] = codex_metadata
        
        with transaction.atomic():
            SelfDrivingTaskIteration.objects.filter(id=config.current_iteration.id).update(
                planning_json=planning_record
            )
        
        config.current_iteration.refresh_from_db(fields=["planning_json"])
        config.log("Stored code versions for Codex-modified files", persisted_code_files)
        config.git.add_files()
    finally:
        common.quietly_delete([stdout_path, stderr_path, last_message_path, plan_path, prompt_path])


def get_guidance_msg(config: SelfDriverConfig):
    return config.guidance


def validate_infrastructure(config):
    for stack_manager in config.all_stack_managers:
        stack_manager.validate_stack()


def validate_all_changed_files(config, normalized_changed, planning_data):
    """Validate all changed files using appropriate validators"""
    validation_errors = []
    
    try:
        validate_infrastructure(config)
    except Exception as e:
        logging.exception(e)
        validation_errors.append(e)
    
    # Build a lookup from file paths to their validator information
    validator_lookup = {}
    if planning_data:
        for code_file_entry in planning_data.get("code_files", []):
            file_path = code_file_entry.get("code_file_path")
            validator = code_file_entry.get("validator")
            if file_path and validator:
                validator_lookup[_normalize_relative_path(file_path)] = validator
    
    for file_path in normalized_changed:
        full_path = config.sandbox_root_dir / file_path
        validator = validator_lookup.get(file_path)
        
        # Skip if file doesn't exist or is not a regular file
        if not full_path.exists() or not full_path.is_file():
            continue
        
        try:
            file_content = full_path.read_text(encoding="utf-8")
            validate_code(
                config,
                full_path,
                file_content,
                validator
            )
        except FileNotFoundError:
            validation_errors.append(BadPlan(f"`{file_path}` is missing after Codex execution; restore the file."))
        except OSError as read_exc:
            validation_errors.append(BadPlan(f"Unable to read `{file_path}` after Codex execution: {read_exc}"))
        except CodeCompilationError as compile_exc:
            validation_errors.append(BadPlan(f"Validation failed for `{file_path}`: {compile_exc}"))
        except Exception as exc:
            validation_errors.append(BadPlan(f"Unexpected validation error for `{file_path}`: {exc}"))
    
    # Return the first validation error, or None if all files are valid
    return common.safe_join(validation_errors, "\n") if validation_errors else None


def _persist_codex_code_versions(
        config: SelfDriverConfig,
        changed_paths: list,
        planning_data: dict
) -> list[str]:
    if not changed_paths:
        return []
    
    instruction_lookup = _build_instruction_lookup(planning_data)
    sandbox_root = config.sandbox_root_dir
    
    persisted = []
    skipped_non_text = []
    
    for rel_path in changed_paths:
        if _should_skip_code_version(rel_path):
            continue
        
        normalized_path = _normalize_relative_path(rel_path)
        absolute_path = sandbox_root / normalized_path
        
        if not absolute_path.exists() or absolute_path.is_dir():
            continue
        
        try:
            common.assert_in_sandbox(
                sandbox_root,
                absolute_path
            )
        except ValueError as ve:
            config.log(
                f"Skipping file outside sandbox when persisting code version: {rel_path}",
                ve
            )
            continue
        
        try:
            CodeFile.update_from_path(
                config.current_iteration,
                absolute_path,
                code_instructions=instruction_lookup.get(normalized_path)
            )
            persisted.append(normalized_path)
        except UnicodeDecodeError:
            skipped_non_text.append(normalized_path)
            config.log(
                f"Skipping non-text file while persisting code version: {normalized_path}"
            )
        except Exception as err:
            config.log(
                f"Failed to persist code version for {normalized_path}",
                err
            )
            raise
    
    if skipped_non_text:
        config.log("Codex change tracking skipped non-text files", skipped_non_text)
    
    return persisted


def _collect_repo_changed_files(
        config: SelfDriverConfig,
        prior_file_checksum_map: dict[Path, int],
        readonly_entries: list
) -> list[Path]:
    current_file_mtime_map = get_file_checksum_map(config.sandbox_root_dir)
    
    read_only_files = [
        config.sandbox_root_dir / e['path']
        for e in readonly_entries
    ]
    
    files = [
        f
        for f, checksum in current_file_mtime_map.items()
        if checksum != prior_file_checksum_map.get(f)
    ]
    
    for f in files:
        if (config.sandbox_root_dir / f) in read_only_files:
            raise BadPlan(f"Codeplanner / writer modified the readonly file '{f}")
    
    return files


def get_file_checksum_map(dir_name: Path) -> dict[Path, int]:
    return {
        f: common.get_checksum(dir_name / f)
        for f in common.iterate_files_deep(dir_name) if not _should_skip_code_version(f)
    }


def _build_instruction_lookup(planning_data: dict | None) -> dict[str, list | dict]:
    lookup: dict[str, list | dict] = {}
    if not planning_data:
        return lookup
    
    for entry in common.ensure_list(planning_data.get("code_files")):
        path = _normalize_relative_path(entry.get("code_file_path"))
        if not path:
            continue
        
        instructions = entry.get("instructions")
        dsl_instructions = entry.get("dsl_instructions")
        
        if instructions:
            lookup[path] = copy.deepcopy(instructions)
        elif dsl_instructions:
            lookup[path] = copy.deepcopy(dsl_instructions)
    
    return lookup


def _normalize_relative_path(path: str | None) -> str:
    if not path:
        return ""
    
    normalized = str(path).strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _should_skip_code_version(relative_path: str) -> bool:
    if not relative_path:
        return True
    
    relative_path = str(relative_path)
    lowered = relative_path.lower()
    if relative_path.split("/", 1)[0] == "artifacts":
        return True
    
    if lowered.endswith(".ds_store"):
        return True
    
    return False


def _extract_codex_usage(
        stdout_text: str | None,
        stderr_text: str | None,
        last_message_path: Path,
        config: SelfDriverConfig
) -> dict:
    metrics: dict[str, float | int] = {}
    sources: list[str] = []
    
    if last_message_path and last_message_path.exists():
        try:
            sources.append(last_message_path.read_text(encoding="utf-8"))
        except Exception:
            ...
    
    if stdout_text:
        sources.append(stdout_text)
    if stderr_text:
        sources.append(stderr_text)
    
    token_records: list[dict] = []
    for text in sources:
        parsed_metrics, token_info = _extract_codex_usage_from_text(text)
        metrics.update({k: v for k, v in parsed_metrics.items() if v is not None})
        if token_info:
            token_records.append(token_info)
        if metrics.get("total_tokens") and metrics.get("total_cost_usd") is not None:
            break
    
    if not metrics.get("total_tokens"):
        last_token_record = next((record for record in reversed(token_records) if record.get("total_tokens") is not None), None)
        if last_token_record:
            metrics["total_tokens"] = last_token_record["total_tokens"]
            # Use breakdown to fill prompt/completion tokens when available.
            if last_token_record.get("input_tokens") is not None:
                metrics.setdefault("prompt_tokens", last_token_record.get("input_tokens"))
            if last_token_record.get("output_tokens") is not None:
                metrics.setdefault("completion_tokens", last_token_record.get("output_tokens"))
            if last_token_record.get("cached_input_tokens") is not None:
                metrics.setdefault("cached_input_tokens", last_token_record.get("cached_input_tokens"))
            if last_token_record.get("reasoning_output_tokens") is not None:
                metrics.setdefault("reasoning_output_tokens", last_token_record.get("reasoning_output_tokens"))
    
    if metrics.get("total_tokens") and metrics.get("total_cost_usd") is None:
        metrics["total_cost_usd"] = _estimate_codex_cost(metrics, config)
    
    return metrics


def _extract_codex_usage_from_text(text: str) -> tuple[dict, dict]:
    metrics: dict[str, float | int | None] = {}
    token_info: dict[str, int | None] = {}
    if not text:
        return metrics, token_info
    
    json_metrics = _extract_codex_usage_from_json(text)
    if json_metrics:
        metrics.update(json_metrics)
        token_info = json_metrics.pop("_token_info", token_info)
        if metrics.get("total_tokens") and metrics.get("total_cost_usd") is not None:
            return metrics, token_info
    
    regex_metrics = _extract_codex_usage_with_regex(text)
    metrics.update({k: v for k, v in regex_metrics.items() if v is not None})
    return metrics, token_info


def _extract_codex_usage_from_json(text: str) -> dict:
    def try_parse_json(candidate: str):
        candidate = candidate.strip()
        if not candidate:
            return None
        try:
            return json.loads(candidate)
        except Exception:
            return None
    
    def find_usage(node):
        if isinstance(node, dict):
            if "usage" in node and isinstance(node["usage"], dict):
                return node["usage"], node
            for value in node.values():
                result = find_usage(value)
                if result:
                    return result
        elif isinstance(node, list):
            for item in node:
                result = find_usage(item)
                if result:
                    return result
        return None
    
    metrics: dict[str, float | int] = {}
    parsed_objects = []
    
    full_obj = try_parse_json(text)
    if full_obj is not None:
        parsed_objects.append(full_obj)
    else:
        for line in text.splitlines():
            obj = try_parse_json(line)
            if obj is not None:
                parsed_objects.append(obj)
    
    for obj in parsed_objects:
        usage_tuple = find_usage(obj)
        if not usage_tuple:
            if isinstance(obj, dict) and obj.get("msg", {}).get("type") == "token_count":
                total_usage = obj["msg"].get("info", {}).get("total_token_usage", {})
                token_info = {
                    "input_tokens": _coerce_int_from_dict(total_usage, ["input_tokens", "inputTokens"]),
                    "cached_input_tokens": _coerce_int_from_dict(total_usage, ["cached_input_tokens", "cachedInputTokens"]),
                    "output_tokens": _coerce_int_from_dict(total_usage, ["output_tokens", "outputTokens"]),
                    "reasoning_output_tokens": _coerce_int_from_dict(total_usage, ["reasoning_output_tokens", "reasoningOutputTokens"]),
                    "total_tokens": _coerce_int_from_dict(total_usage, ["total_tokens", "totalTokens"])
                }
                if any(v is not None for v in token_info.values()):
                    metrics["_token_info"] = token_info
                    if token_info.get("total_tokens") is not None:
                        metrics.setdefault("total_tokens", token_info["total_tokens"])
                    if token_info.get("input_tokens") is not None:
                        metrics.setdefault("prompt_tokens", token_info["input_tokens"])
                    if token_info.get("output_tokens") is not None:
                        metrics.setdefault("completion_tokens", token_info["output_tokens"])
                    if token_info.get("cached_input_tokens") is not None:
                        metrics.setdefault("cached_input_tokens", token_info["cached_input_tokens"])
                    if token_info.get("reasoning_output_tokens") is not None:
                        metrics.setdefault("reasoning_output_tokens", token_info["reasoning_output_tokens"])
            continue
        usage_dict, container = usage_tuple
        prompt_tokens = _coerce_int_from_dict(usage_dict, ["prompt_tokens", "input_tokens", "promptTokens"])
        completion_tokens = _coerce_int_from_dict(usage_dict, ["completion_tokens", "output_tokens", "completionTokens"])
        total_tokens = _coerce_int_from_dict(usage_dict, ["total_tokens", "totalTokens"])
        cost_usd = _coerce_float_from_dict(
            usage_dict,
            ["total_cost", "total_cost_usd", "cost", "usd_cost", "totalCost", "totalCostUsd"]
        )
        if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
            total_tokens = prompt_tokens + completion_tokens
        if cost_usd is None:
            cost_usd = _coerce_float_from_dict(
                container,
                ["total_cost", "total_cost_usd", "cost", "usd_cost", "totalCost", "totalCostUsd"]
            )
        
        if prompt_tokens is not None:
            metrics["prompt_tokens"] = prompt_tokens
        if completion_tokens is not None:
            metrics["completion_tokens"] = completion_tokens
        if total_tokens is not None:
            metrics["total_tokens"] = total_tokens
        if cost_usd is not None:
            metrics["total_cost_usd"] = cost_usd
    
    return metrics


def _extract_codex_usage_with_regex(text: str) -> dict:
    metrics: dict[str, float | int | None] = {}
    if not text:
        return metrics
    
    prompt_match = re.search(r"(?:prompt|input)\s+tokens?\s*[:=]\s*(\d+)", text, re.IGNORECASE)
    completion_match = re.search(r"(?:completion|output)\s+tokens?\s*[:=]\s*(\d+)", text, re.IGNORECASE)
    total_match = re.search(r"total\s+tokens?(?:\s+used)?\s*[:=]\s*(\d+)", text, re.IGNORECASE)
    
    cost_match = re.search(
        r"(?:total\s+cost|cost)\s*[:=]\s*\$?\s*([0-9]+(?:\.[0-9]+)?)",
        text,
        re.IGNORECASE
    )
    
    if prompt_match:
        metrics["prompt_tokens"] = int(prompt_match.group(1))
    if completion_match:
        metrics["completion_tokens"] = int(completion_match.group(1))
    if total_match:
        metrics["total_tokens"] = int(total_match.group(1))
    elif metrics.get("prompt_tokens") is not None and metrics.get("completion_tokens") is not None:
        metrics["total_tokens"] = metrics["prompt_tokens"] + metrics["completion_tokens"]
    
    if cost_match:
        try:
            metrics["total_cost_usd"] = float(cost_match.group(1))
        except ValueError:
            ...
    
    return metrics


def _estimate_codex_cost(metrics: dict, config: SelfDriverConfig) -> float | None:
    total_tokens = metrics.get("total_tokens")
    if not total_tokens:
        return None
    
    # Default to the planner's configured model; fall back to the primary system planning model.
    planning_model = getattr(config, "model_code_planning", None) or LlmModel.OPENAI_GPT_5
    planning_model = LlmModel(planning_model)
    
    pricing = MODEL_PRICE_USD_PER_MILLION_TOKENS.get(planning_model)
    if not pricing:
        return None
    
    token_breakdown = {
        "prompt_tokens": metrics.get("prompt_tokens"),
        "completion_tokens": metrics.get("completion_tokens"),
        "cached_input_tokens": metrics.get("cached_input_tokens")
    }
    prompt_tokens = common.safe_positive_int(token_breakdown.get("prompt_tokens"))
    completion_tokens = common.safe_positive_int(token_breakdown.get("completion_tokens"))
    cached_tokens = common.safe_positive_int(token_breakdown.get("cached_input_tokens")) or 0
    
    if prompt_tokens is None and completion_tokens is None:
        prompt_tokens = total_tokens
        completion_tokens = 0
    elif prompt_tokens is None:
        prompt_tokens = max(total_tokens - completion_tokens, 0)
    elif completion_tokens is None:
        completion_tokens = max(total_tokens - prompt_tokens, 0)
    
    prompt_billable = max(prompt_tokens - cached_tokens, 0)
    completion_billable = completion_tokens
    
    cost_input = prompt_billable * pricing.get("input", 0) / 1_000_000
    cost_output = completion_billable * pricing.get("output", 0) / 1_000_000
    
    return round(cost_input + cost_output, 6)


def _coerce_int_from_dict(data: dict, keys: list[str]) -> int | None:
    for key in keys:
        if key in data:
            value = data.get(key)
            try:
                if isinstance(value, bool):
                    continue
                if isinstance(value, (int, float)):
                    return int(value)
                if isinstance(value, str) and value.strip().isdigit():
                    return int(value.strip())
            except Exception:
                continue
    return None


def _coerce_float_from_dict(data: dict, keys: list[str]) -> float | None:
    for key in keys:
        if key in data:
            value = data.get(key)
            try:
                if value is None or isinstance(value, bool):
                    continue
                if isinstance(value, (int, float)):
                    return float(value)
                if isinstance(value, str):
                    cleaned = value.strip().replace("$", "")
                    return float(cleaned)
            except Exception:
                continue
    return None


def plan_code_changes(config):
    config.set_phase(SdaPhase.PLANNING)
    
    planning_data = None
    
    config.log(f"PHASE - plan_code_changes: {config.current_iteration.id}")
    if not config.self_driving_task.initial_tests_pass:
        # ok the tests for exists tasks pass, but we don't have a test for this task.  write it now
        SelfDrivingTaskIteration.objects.filter(id=config.current_iteration.id).update(
            routing_json={
                "recovery_path_explanation": "previous task's tests have regressed",
                "recovery_path": DevelopmentRoutingPath.DIRECT_FIX,
                "classification": "previous task's tests have regressed.  need to fix before we do anything else"
            }
        )
        config.current_iteration.refresh_from_db(fields=["routing_json"])
        
        planning_data = plan_test_fixing_code_changes(config)
    else:
        route_to = route_code_changes(config)
        
        if route_to in [DevelopmentRoutingPath.DIRECT_FIX, DevelopmentRoutingPath.AWS_PROVISIONING_PLANNER] and config.guidance:
            config.log(f"rerouting dev path from {route_to} to {DevelopmentRoutingPath.ESCALATE_TO_PLANNER} as we have task level guidance")
            route_to = DevelopmentRoutingPath.ESCALATE_TO_PLANNER
        
        if DevelopmentRoutingPath.ESCALATE_TO_PLANNER.eq(route_to):
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


def get_strategic_unblocking_data(config):
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
            get_relevant_code_files(config),
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
            get_tasktype_specific_instructions(config),
            get_goal_msg(config, "Please think of ways to unblock the agent from reaching this goal ")
        ],
        output_schema="codeplanning--strategic_unblocker.md.schema.json",
        tag_entity=config.current_iteration
    ).json()


def validate_plan(config: SelfDriverConfig, planning_data):
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


def bootstrap_selfdriving_agent(task_id) -> SelfDrivingTask:
    task = Task.objects.get(id=task_id)
    
    initiative_has_iterations = SelfDrivingTaskIteration.objects.filter(
        self_driving_task__task__initiative_id=task.initiative_id
    ).exists()
    
    Task.objects.filter(id=task.id).update(
        status=TaskStatus.IN_PROGRESS
    )
    
    self_driving_task: SelfDrivingTask = task.create_self_driving_env()
    
    if not initiative_has_iterations:
        # no tests
        self_driving_task.initial_tests_pass = True
        self_driving_task.save()
    
    with SelfDriverConfig(self_driving_task) as config:
        config.set_phase(SdaPhase.INIT)
        
        self_driving_task.get_git().pull()
        config.iterate_if_necessary()
        
        if not config.business.codefile_set.exists():
            config.git.mk_venv()
            config.business.snapshot_code(
                config.current_iteration,
                include_erie_common=True
            )
        
        elif not self_driving_task.selfdrivingtaskiteration_set.filter(evaluation_json__isnull=False).exists():
            config.business.snapshot_code(
                config.current_iteration,
                include_erie_common=False
            )
    
    return self_driving_task


def ensure_lb_alias_record(config: SelfDriverConfig) -> None:
    if EnvironmentType.PRODUCTION.eq(config.env_type):
        domain_name = config.business.domain
    else:
        domain_name = config.initiative.domain
    
    hosted_zone_id = domain_manager.find_hosted_zone_id(config.business.domain)
    
    if not domain_name or not hosted_zone_id:
        raise Exception(f"missing domain ({domain_name}) or hosted zone id ({hosted_zone_id})")
    
    load_balancer_arn = common.first(OpenTofuStackManager.get_cross_stack_arns(config.all_stack_managers, "aws_lb"))
    if not load_balancer_arn:
        config.log(f"No Application Load Balancer found in stacks {config.get_stack_names()}; skipping DNS alias update")
        return
    
    elbv2_client = boto3.client("elbv2", region_name=config.env_type.get_aws_region())
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
        domain_manager.upsert_subdomain_alias(
            hosted_zone_id=hosted_zone_id,
            record_name=domain_name,
            target_dns_name=dns_name,
            target_hosted_zone_id=canonical_zone_id,
            comment=f"Auto-configured by Erie Iron for task {config.self_driving_task.task_id}",
            dual_stack=dual_stack
        )
        
        domain_manager.wait_for_dns_propagation(
            domain_name,
            dns_name
        )
    except Exception as exc:
        logging.exception("Failed to upsert Route53 alias for %s", domain_name)
        raise AgentBlocked(f"failed to configure Route53 alias for {domain_name}: {exc}")
    
    config.log(f"Ensured Route53 alias for {domain_name} targets {dns_name}")


def build_container_image(
        config: SelfDriverConfig,
        container_env: dict,
        container_file: Path
) -> str:
    exec_container_prune()
    ensure_container_storage_capacity(config)
    
    current_iteration = config.current_iteration
    self_driving_task = current_iteration.self_driving_task
    
    container_image_tag_parts = [
        self_driving_task.business.name,
        self_driving_task.id,
        current_iteration.version_number
    ]
    
    # force a new container image tag to make sure OpenTofu updates
    if config.one_off_action:
        container_image_tag_parts.append(str(time.time())[-5:])
    
    container_image_tag = sanitize_aws_name(container_image_tag_parts, max_length=128)
    
    config.log(f"\n\n\n\n======== Begining PODMAN Build for tag {container_image_tag} ")

    config.log(f"Building container image for platform: {ContainerPlatform.FARGATE}")
    container_build_cmd = common.strings([
        "podman",
        "build",
        "--memory", "4g",
        "--memory-swap", "10g",
        "--platform", ContainerPlatform.FARGATE,
        "--build-arg", f"ERIEIRON_PUBLIC_COMMON_SHA={ERIEIRON_PUBLIC_COMMON_VERSION}",
        "-t", container_image_tag,
        "-f", container_file,
        container_file.parent
    ])
    
    config.log(f"\n\nstarting podman build with the command:\n{' '.join(container_build_cmd)}\n\n")
    build_process = subprocess.Popen(
        container_build_cmd,
        stdout=config.log_f,
        stderr=subprocess.STDOUT,
        text=True,
        env=container_env
    )
    
    while build_process.poll() is None:
        time.sleep(1)
    
    if build_process.returncode != 0:
        handle_podman_build_failure(config, build_process.returncode)
    
    push_image_to_ecr(
        config,
        container_image_tag
    )
    
    return container_image_tag


def ensure_container_storage_capacity(
        config: SelfDriverConfig,
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


def handle_podman_build_failure(config: SelfDriverConfig, return_code: int) -> None:
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


def get_env_var_names(config: SelfDriverConfig) -> str:
    env = build_env(config)
    return ", ".join(env.keys())


def build_env(config: SelfDriverConfig) -> dict:
    env_type = config.env_type
    aws_region = env_type.get_aws_region()
    
    aws_credentials = botocore.session.Session(
        profile=os.environ.get("AWS_PROFILE")
    ).get_credentials().get_frozen_credentials()
    
    stack_foundation, stack_application = get_stacks(config)
    
    if EnvironmentType.PRODUCTION.eq(config.env_type):
        domain_name = config.business.domain
    else:
        domain_name = config.initiative.domain
    
    env = {
        "DOMAIN_NAME": domain_name,
        "ERIE_IRON_ENV": config.env_type.value,
        "AWS_DEFAULT_REGION": settings.AWS_DEFAULT_REGION_NAME,
        "AWS_ACCOUNT_ID": settings.AWS_ACCOUNT_ID,
        "AWS_ACCESS_KEY_ID": aws_credentials.access_key,
        "AWS_SECRET_ACCESS_KEY": aws_credentials.secret_key,
        "AWS_SESSION_TOKEN": aws_credentials.token,
        "LLM_API_KEYS_SECRET_ARN": settings.LLM_API_KEYS_SECRET_ARN,
        
        "STACK_NAME": stack_application.stack_name,
        "FOUNDATION_STACK_NAME": stack_foundation.stack_name,
        
        "TASK_NAMESPACE": stack_application.stack_namespace_token,
        "STACK_IDENTIFIER": stack_application.stack_namespace_token,
        "FOUNDATION_STACK_IDENTIFIER": stack_foundation.stack_namespace_token,
        
        "BUILDAH_FORMAT": "docker",
        "PATH": os.getenv("PATH")
    }

    hf_model_cache_s3_uri = getattr(settings, "HF_MODEL_CACHE_S3_URI", None)
    if hf_model_cache_s3_uri:
        env["HF_MODEL_CACHE_S3_URI"] = hf_model_cache_s3_uri
    
    for credential_service_name, cred_def in config.business.required_credentials.items():
        if credential_service_name == CredentialService.RDS.value:
            # OpenTofu and RDS handle the RDS secret - we update this as a special case later
            continue
        
        secret_arn_env_var = cred_def.get("secret_arn_env_var")
        secrent_arn = credential_manager.manage_credentials(
            config,
            env_type,
            credential_service_name,
            cred_def
        )
        env[secret_arn_env_var] = secrent_arn
    
    for k in list(env.keys()):
        if k.startswith("__") or env.get(k) is None:
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


def build_lambda_packages(config: SelfDriverConfig, ) -> list[dict]:
    s3 = aws_utils.client("s3")
    
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
    
    for resource_definition in OpenTofuStackManager.get_cross_stack_resource_definitions([config.stack_managers[InfrastructureStackType.APPLICATION]], "aws_lambda_function"):
        s3_key_ref = resource_definition['s3_key']
        resource_name = "todo"
        if s3_key_ref:
            raise AgentBlocked('fix this')
        
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
    for resource_def in OpenTofuStackManager.get_cross_stack_resource_definitions(config.all_stack_managers, "aws_s3_bucket"):
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
            "properties": properties,
            "metadata": resource_config.get("Metadata", {}) or {},
        })
    
    return buckets


def get_infrastructure_yaml_data(config: SelfDriverConfig, cf_template: InfrastructureStackType) -> dict:
    return agent_tools.parse_cloudformation_yaml(
        get_infrastructure_yaml_code(config, cf_template)
    )


def get_infrastructure_yaml_codeversion(config, cf_template: InfrastructureStackType):
    return CodeFile.get(
        config.business,
        cf_template.value
    ).get_latest_version(
        config.self_driving_task
    )


def get_infrastructure_yaml_code(config, cf_template: InfrastructureStackType):
    infrastructure_code_version = get_infrastructure_yaml_codeversion(config, cf_template)
    
    return infrastructure_code_version.code if infrastructure_code_version else None


def latest_tag_from_ecr(config: SelfDriverConfig) -> str:
    region = config.env_type.get_aws_region()
    ecr_client = boto3.client("ecr", region_name=region)
    account_id = aws_utils.client("sts").get_caller_identity()["Account"]
    
    repo_name = sanitize_aws_name(config.business.service_token)
    
    try:
        ecr_client.describe_repositories(repositoryNames=[repo_name])
    except ecr_client.exceptions.RepositoryNotFoundException:
        raise RuntimeError(f"ECR repository {repo_name} not found")
    
    try:
        response = ecr_client.describe_images(
            repositoryName=repo_name,
            maxResults=50
        )
        
        if not response.get('imageDetails'):
            raise RuntimeError(f"No images found in ECR repository {repo_name}")
        
        latest_image = max(response['imageDetails'], key=lambda x: x['imagePushedAt'])
        
        return latest_image['imageDigest']
    
    except Exception as e:
        config.log(f"Error retrieving latest image from ECR: {e}")
        raise


def push_image_to_ecr(
        config: SelfDriverConfig,
        container_image_tag: str
):
    region = config.env_type.get_aws_region()
    ecr_client = boto3.client("ecr", region_name=region)
    repo_name = config.ecr_repo_name
    
    full_image_uri = get_full_image_uri(
        repo_name,
        container_image_tag,
        region
    )
    
    config.log(f"\n\n\n\n======== Begining ECR Push to {full_image_uri} ")
    
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
                stderr=subprocess.STDOUT
            )
            
            env = os.environ.copy()
            env.pop("HTTP_PROXY", None)
            env.pop("http_proxy", None)
            env.pop("HTTPS_PROXY", None)
            env.pop("https_proxy", None)
            
            subprocess.run(
                ["podman", "push", full_image_uri],
                check=True,
                stdout=config.log_f,
                stderr=subprocess.STDOUT,
                env=env
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
    
    return full_image_uri


def run_container_command(
        config: SelfDriverConfig,
        command_args: list[str],
        container_env: dict,
        container_image_tag: str
) -> None:
    command_args = common.ensure_list(command_args)

    cmd = [
        "podman", "run", "--rm",
        "--memory", "4g",
        "--memory-swap", "10g",
        "--platform", ContainerPlatform.FARGATE,
        "-v", f"{config.sandbox_root_dir}:/app",
        "-w", "/app",
        *build_env_flags(container_env),
        container_image_tag,
        "python", "manage.py",
        *command_args
    ]
    
    config.log("\n" + "=" * 50 + "\n")
    config.log(f"RUNNING {' '.join(cmd)} in {config.sandbox_root_dir}\n")
    print(f"DUDE {' '.join(cmd)} in {config.sandbox_root_dir}\n")
    config.log("=" * 50 + "\n")
    
    # Capture podman run start time
    process = subprocess.Popen(
        common.strings(cmd),
        stdout=config.log_f,
        env=container_env,
        stderr=subprocess.STDOUT,
        text=True
    )
    
    config.log(f"Podman {command_args[-1]} execution started with PID {process.pid}")
    
    # Wait for completion
    while process.poll() is None:
        time.sleep(2)
    
    return_code = process.returncode
    
    if return_code == 0:
        logging.info(f"\n{command_args[-1]} execution completed with return code: {return_code}\n")
    elif return_code == 137:
        raise ExecutionException(f"\n{command_args[-1]} execution was killed with SIGKILL (exit code 137). Possible Out-Of-Memory condition.\n")
    else:
        raise ExecutionException(f"\n{command_args[-1]} execution completed with return code: {return_code}\n")


def build_deploy_exec_iteration(config: SelfDriverConfig, attempt=0) -> str:
    deployment_start_epoch = int(time.time())
    try:
        config.current_iteration.evaluation_json = None
        config.current_iteration.save()
        
        for stack_manager in config.all_stack_managers:
            stack_manager.validate_stack()
        
        container_env = build_env(
            config
        )
        
        container_image_tag, lambda_datas = build_iteration(
            config,
            container_env
        )
        
        deploy_iteration(
            config,
            container_env,
            container_image_tag,
            lambda_datas
        )
        
        execute_iteration(
            config,
            container_env,
            container_image_tag
        )
        
        config.log("Execution finished")
    except NeedPlan as e:
        raise e
    except AgentBlocked as e:
        logging.exception(e)
        raise e
    except OpenTofuException as e:
        logging.exception(e)
        config.log(f"OpenTofu deployment error: {e}")
    except FailingTestException as e:
        config.log(f"Tests are failing.  **review sysout logs for details**")
    except Exception as e:
        config.log(common.get_stack_trace_as_string(e))
    finally:
        store_deploy_and_execution_logs(
            config,
            deployment_start_epoch
        )
        
        exec_container_prune()
        
        config.business.snapshot_code(
            config.current_iteration,
            include_erie_common=False
        )


def store_deploy_and_execution_logs(
        config: SelfDriverConfig,
        deployment_start_epoch: int
) -> None:
    config.current_iteration.log_content_cloudwatch = aws_log_reader.get_cloudwatch_content(
        config.all_stack_tokens,
        deployment_start_epoch
    )
    
    deployment_logs = common.get_dict(config.deployment_logs)
    deploy_errors = []
    for stack_type, stack_results in deployment_logs.items():
        for stack_result in stack_results:
            for plan_result in common.ensure_list(stack_result.get("plan_results", [])):
                if not plan_result.get("stderr"):
                    continue
                deploy_errors.append({
                    "stack": stack_type,
                    "stage": plan_result.get("stage"),
                    "stderr": plan_result.get("stderr")
                })
    
    if not deploy_errors:
        deploy_errors.append("No deployment errors.  Deploment completed successfully for all stacks")
    
    config.current_iteration.log_content_deployment = {
        "schema": "opentofu/v1",
        "deployment_window_start": datetime.fromtimestamp(deployment_start_epoch, tz=dt_timezone.utc).isoformat(),
        "deployment_logs": deployment_logs,
        "deploy_errors": deploy_errors
    }
    config.current_iteration.save(update_fields=["log_content_deployment", "log_content_cloudwatch"])


def execute_iteration(
        config: SelfDriverConfig,
        container_env: dict,
        container_image_tag: str
):
    config.set_phase(SdaPhase.EXECUTION)
    
    task = config.task
    task_type = config.task_type
    self_driving_task = config.self_driving_task
    
    if TaskType.CODING_ML.eq(task_type):
        run_container_command(
            config=config,
            container_env=container_env,
            command_args=self_driving_task.main_name,
            container_image_tag=container_image_tag
        )
        config.log_f.flush()  # Ensure ML execution logs are visible to tailing thread
    elif task_type.eq(TaskType.PRODUCTION_DEPLOYMENT):
        logging.info("Skipping automated test run for production deployment task")
        # TODO - is it possible to block until the new ECS task is running?
    elif TaskType.TASK_EXECUTION.eq(task_type) and TaskExecutionSchedule.ONCE.eq(task.execution_schedule):
        task_io_dir = Path(config.sandbox_root_dir) / "task_io"
        task_io_dir.mkdir(parents=True, exist_ok=True)
        
        input_file = task_io_dir / f"{task.id}-input.json"
        common.write_json(input_file, task.get_upstream_outputs())
        
        output_file = task_io_dir / f"{task.id}-output.json"
        
        run_container_command(
            config=config,
            container_env=container_env,
            command_args=[
                self_driving_task.main_name,
                "--input_file", input_file,
                "--output_file", output_file
            ],
            container_image_tag=container_image_tag
        )
    elif task_type in [TaskType.CODING_APPLICATION, TaskType.DESIGN_WEB_APPLICATION, TaskType.INITIATIVE_VERIFICATION]:
        run_automated_tests(
            config,
            container_env,
            container_image_tag
        )
    else:
        logging.info(f"nothing to execute for task type {task_type}")


def run_automated_tests(config: SelfDriverConfig, container_env: dict, container_image_tag: str):
    import random
    import time
    random.seed(time.time())
    
    test_errors_blob = common.get(config.iteration_to_modify, ["evaluation_json", "test_errors"])
    
    # try:
    #     empty_stack_buckets(
    #         config,
    #         delete_bucket=False
    #     )
    # except Exception as e:
    #     config.log(e)
    #     raise AgentBlocked(f"unable to empty buckets for stack {config.task.id}")
    
    if test_errors_blob:
        first_tests = llm_chat(
            "parse test failures",
            [
                LlmMessage.sys(textwrap.dedent("""
                    Parse the failing tests from the supplied log output.  
                    format the test name as fully qualified test_module.test_method
                    return a list of all failing tests using the following format
                    ```json
                    {
                        "failing_tests": [
                            'core.tests.test_task_bug_report_articleparsernew_t57y4lei.ForwardToDigestAcceptanceTests.test_s3_upload_triggers_digest_job_enqueue'
                        ]
                    }
                """)),
                LlmMessage.user_from_data("Tests log output", test_errors_blob)
            ],
            tag_entity=config.current_iteration,
            model=LlmModel.OPENAI_GPT_5_NANO,
            code_response=True
        ).json().get("failing_tests")
    elif config.self_driving_task.test_file_path:
        test_label = config.self_driving_task.test_file_path
        first_tests = [
            test_label.replace("/", ".").removesuffix(".py").lstrip(".")
        ]
    else:
        first_tests = None
    
    if first_tests:
        config.log(f"Running task's automated test first: {first_tests}")
        try:
            run_container_command(
                config=config,
                container_env=container_env,
                command_args=["test", "--keepdb", "--noinput", *first_tests],
                container_image_tag=container_image_tag
            )
            config.log(f"{first_tests} PASSED. Proceeding to full test suite.")
        except ExecutionException as e:
            raise FailingTestException(f"Some or all of {first_tests} failed. See logs above for details.")
    
    config.log(
        "Running the test suite three times to detect flakiness. "
        "If all three runs pass, tests are considered stable. "
        "If some runs pass and some fail, tests are flaky. "
        "If all runs fail, tests are broken."
    )
    results = []
    for i in range(3):
        time.sleep(random.uniform(0.5, 1.5))
        try:
            run_container_command(
                config=config,
                container_env=container_env,
                command_args=["test", "--keepdb", "--noinput"],
                container_image_tag=container_image_tag
            )
            config.log(f"Test suite PASS on run {i + 1} of 3.")
            results.append(True)
        except ExecutionException as e:
            if i == 0:
                raise FailingTestException(f"FIRST test run failed.  See logs above for details.")
            else:
                config.log(f"Test suite FAILED on run {i + 1} of 3. See logs above for details.")
                results.append(False)
    
    passes = sum(results)
    if passes == 3:
        config.log("ALL TESTS PASS ON ALL THREE RUNS")
        
        if not config.self_driving_task.initial_tests_pass:
            config.self_driving_task.initial_tests_pass = True
            config.self_driving_task.save()
    elif passes == 0:
        config.log("TESTS FAILED ON ALL THREE RUNS")
        raise FailingTestException("All test runs failed")
    else:
        config.log(
            "TESTS PASSED ON SOME RUNS BUT FAILED ON OTHERS. "
            "This indicates flakiness. Please review the test code for flakiness risks and fix."
        )
        raise FailingTestException("Some test runs failed - flaky tests")


def deploy_iteration(
        config: SelfDriverConfig,
        container_env: dict,
        container_image_tag: str,
        lambda_datas: list
) -> dict[str, Any]:
    config.set_phase(SdaPhase.DEPLOY)
    task = config.task
    
    validate_infrastructure(config)
    
    foundation_outputs = deploy_opentofu_stack(
        config=config,
        stack_type=InfrastructureStackType.FOUNDATION,
        container_image_tag=container_image_tag,
        container_env=container_env,
    )
    
    add_rds_vals_to_env(
        config.business,
        container_env,
        foundation_outputs
    )
    
    manage_db(
        config,
        container_env,
        container_image_tag
    )
    
    validate_web_container(
        config,
        container_env,
        container_image_tag
    )
    
    app_outputs = deploy_opentofu_stack(
        config=config,
        stack_type=InfrastructureStackType.APPLICATION,
        container_env=container_env,
        container_image_tag=container_image_tag,
        lambda_datas=lambda_datas,
        previous_stack_outputs=foundation_outputs
    )
    
    ensure_lb_alias_record(config)


def add_rds_vals_to_env(business: Business, container_env: dict, foundation_outputs: dict):
    if not foundation_outputs:
        raise BadPlan("Foundation infrastructure stack lacks RDS outputs required for environment configuration")
    
    rds_secret_arn = foundation_outputs.get("RdsMasterSecretArn")
    if not rds_secret_arn:
        raise BadPlan("Infrastructure stack lacks an output named RdsMasterSecretArn")
    
    if not business.required_credentials:
        raise BadPlan(f"Business missing required_credentials: {business.service_token}")
    
    rds_credential_def = business.required_credentials.get(CredentialService.RDS.value) or {}
    secret_arn_env_var = rds_credential_def.get("secret_arn_env_var")
    if not secret_arn_env_var:
        raise BadPlan("Business is missing required RDS credential definition or secret_arn_env_var")
    
    container_env[secret_arn_env_var] = rds_secret_arn
    
    output_varname_to_envname = {
        "RdsInstanceDBName": "ERIEIRON_DB_NAME",
        "RdsInstancePort": "ERIEIRON_DB_PORT",
        "RdsInstanceEndpoint": "ERIEIRON_DB_HOST"
    }
    for output_var_name, env_name in output_varname_to_envname.items():
        output_var_value = foundation_outputs.get(output_var_name)
        if not output_var_value:
            raise BadPlan(f"Infrastructure stack lacks an output value for the required output named '{output_var_name}'")
        container_env[env_name] = str(output_var_value)


def get_iteration_files_msg(current_iteration):
    modified_files = list(current_iteration.codeversion_set.all())
    if modified_files:
        return LlmMessage.user_from_data(
            "Modified Files", [
                cv.code_file.file_path
                for cv in modified_files
            ], "modified_file")
    else:
        return "No files modified during this iteration"


def build_iteration(config, container_env):
    config.set_phase(SdaPhase.BUILD)
    iteration = config.current_iteration
    task_execution = init_task_execution(iteration)
    
    if config.current_iteration.version_number > 1:
        required_build_steps = llm_chat(
            "Plan Build Steps",
            [
                get_sys_prompt("build_planner.md"),
                get_iteration_files_msg(config.current_iteration),
            ],
            output_schema="build_planner.md.schema.json",
            model=LlmModel.OPENAI_GPT_5_NANO,
            tag_entity=config.current_iteration,
            reasoning_effort=LlmReasoningEffort.LOW
        ).json()
    else:
        required_build_steps = {
            BuildStep.CONTAINERS.value: True,
            BuildStep.LAMBDAS.value: True
        }
    
    required_build_steps = {
        BuildStep.CONTAINERS.value: True,
        BuildStep.LAMBDAS.value: False
    }
    
    if required_build_steps.get(BuildStep.LAMBDAS.value):
        lambda_datas = build_lambda_packages(
            config
        )
    else:
        lambda_datas = []
    
    previous_container_tag = None # config.current_iteration.docker_tag or config.iteration_to_modify.docker_tag
    tag_exists_in_ecr = aws_utils.tag_exists_in_ecr(
        config.ecr_repo_name,
        previous_container_tag,
        config.env_type.get_aws_region()
    )
    
    if (
            tag_exists_in_ecr or
            (previous_container_tag and not required_build_steps.get(BuildStep.CONTAINERS.value))
    ):
        container_image_tag = previous_container_tag
    else:
        docker_file = config.sandbox_root_dir / "Dockerfile"
        
        ecr_authenticate(
            config,
            docker_file
        )
        
        container_image_tag = build_container_image(
            config,
            container_env,
            docker_file
        )
    
    SelfDrivingTaskIteration.objects.filter(id=iteration.id).update(
        docker_tag=container_image_tag
    )
    iteration.refresh_from_db(fields=["docker_tag"])
    
    return container_image_tag, lambda_datas


def template_modified_this_iteration(
        config: SelfDriverConfig,
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


def is_lambdas_modified(config: SelfDriverConfig):
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


def manage_db(
        config: SelfDriverConfig,
        container_env: dict,
        container_image_tag: str
):
    try:
        run_container_command(
            config=config,
            container_env=container_env,
            command_args=["makemigrations", "--noinput"],
            container_image_tag=container_image_tag
        )
        
        run_container_command(
            config=config,
            container_env=container_env,
            command_args=["migrate"],
            container_image_tag=container_image_tag
        )
    except Exception as e:
        logging.exception(e)
        if "DuplicateDatabase" in str(e):
            raise AgentBlocked(e.__dict__)
        else:
            raise DatabaseMigrationException(e)


def validate_web_container(
        config: SelfDriverConfig,
        container_env: dict,
        container_image_tag: str
):
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
                *build_env_flags(container_env),
                container_image_tag
            ],
            stdout=config.log_f,
            stderr=subprocess.STDOUT,
            env=container_env,
            text=True
        )
        
        config.log(f"Starting Webcontainer Validation (PID={process.pid})")
        start_time = time.time()
        max_wait = 60  # seconds
        healthy = False
        for attempt in range(1, 13):  # 12 attempts × 5s = 60s
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


def assert_tests_green(config: SelfDriverConfig):
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


def compute_goal_achievement_gate(config: SelfDriverConfig, ) -> tuple[bool, str]:
    iteration = config.current_iteration
    
    if not iteration.log_content_deployment:
        return False, "OpenTofu plan/apply logs were not captured. You may not declare goal complete."
    
    if common.get(iteration.log_content_deployment, "deploy_errors") and "No deployment errors" not in common.first(common.get(iteration.log_content_deployment, "deploy_errors")):
        return False, "OpenTofu plan/apply reported errors. Resolve them before declaring success."
    
    if config.current_iteration.version_number == 1:
        return False, "We have not written any code yet for this task"
    
    if not (config.self_driving_task.test_file_path and (config.sandbox_root_dir / config.self_driving_task.test_file_path).exists()):
        return False, "this task does not yet have an automated test.  Need to write an automated test and make it pass before allowing goal achieved"
    
    return True, "we've written code and it deployed successfully"


def evaluate_iteration(
        config: SelfDriverConfig
):
    return
    log_output = config.set_phase(SdaPhase.EVALUATE)
    if "no space left on device" in common.default_str(log_output).lower():
        subprocess.run(["podman", "system", "prune", "-a", "-f"], check=True)
        raise RetryableException(f"execution is failing with 'no space left on device'\n\n{log_output}.  I just pruned the containers, so should be cleared up now.")
    
    iteration = config.current_iteration
    
    deploy_errors = common.get(iteration.log_content_deployment, "deploy_errors")
    runtime_errors = common.get(iteration.log_content_cloudwatch, "errors")
    
    logs_data = {
        "Deployment Errors": deploy_errors,
        "Runtime Errors (cloudwatch)": runtime_errors
    }
    
    if iteration.log_content_execution:
        logs_data["Runtime Logs (other)"] = iteration.log_content_execution
    
    iteration.exceptions = llm_chat(
        "Error Extraction",
        [
            get_sys_prompt("log_parser_tofu.md"),
            LlmMessage.user_from_data("Logs", logs_data)
        ],
        model=LlmModel.OPENAI_GPT_5_MINI,
        tag_entity=config.current_iteration
    ).text
    iteration.save()
    
    if TaskType.PRODUCTION_DEPLOYMENT.eq(config.task_type):
        goal_achieved_critera = textwrap.dedent(f"""
            - Set `"goal_achieved": true` only if the logs contain no errors and the OpenTofu plan/apply completed successfully
            - This is a production deployment task, so no automated tests should have been run
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
        """)
    
    if (
            TaskType.PRODUCTION_DEPLOYMENT.eq(config.task_type)
            and iteration.log_content_deployment
            and not (runtime_errors or deploy_errors)
    ):
        raise GoalAchieved(f"Production deployment succeeded for {config.business.domain}")
    
    allow_goal_achieved, goal_achieved_reason = compute_goal_achievement_gate(
        config
    )
    
    eval_data = llm_chat(
        "Iteration Summarizer",
        [
            get_sys_prompt([
                "iteration_summarizer_tofu.md",
                "common--iam_role_tofu.md",
                "common--agent_provided_functionality_tofu.md",
                "common--domain_management_tofu.md",
                "common--forbidden_actions_tofu.md",
                "common--environment_variables_tofu.md"
            ], replacements=[
                ("<env_vars>", get_env_var_names(config)),
                ("<goal_achieved_critera>", goal_achieved_critera)
            ]),
            
            get_goal_msg(
                config,
                "Task Goal"
            ),
            
            get_previous_iteration_summaries_msg(
                config
            ),
            
            LlmMessage.user_from_data(
                "Allow returning `Goal Achieved`?",
                {
                    "allow_goal_achieved": allow_goal_achieved,
                    "allow_goal_achieved_justification": goal_achieved_reason
                }
            ),
            
            get_logs_msg(
                config,
                config.current_iteration
            ),
            
            LlmMessage.user(textwrap.dedent(
                f"""
                **QUICK REFERENCE** Exceptions extracted from the logs.  If there are problems, most likely related to the following:
                
                {iteration.exceptions}
                """
            )) if iteration.exceptions else None,
            
            LlmMessage.user(
                "Please summarize this iteration"
            )
        ],
        tag_entity=config.current_iteration,
        output_schema="iteration_summarizer_tofu.md.schema.json"
    ).json()
    
    if eval_data.get("is_stagnating"):
        config.current_iteration.strategic_unblocking_json = get_strategic_unblocking_data(config)
        config.current_iteration.save()
    
    with transaction.atomic():
        SelfDrivingTaskIteration.objects.filter(id=iteration.id).update(
            evaluation_json=eval_data
        )
        iteration.refresh_from_db(fields=["evaluation_json"])
    
    if eval_data.get("blocked"):
        raise AgentBlocked(eval_data)
    
    if not allow_goal_achieved:
        eval_data['goal_achieved'] = False
    
    with transaction.atomic():
        SelfDrivingTaskIteration.objects.filter(id=iteration.id).update(
            evaluation_json=eval_data
        )
    
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
    
    extract_lessons(
        config,
        "code deploy and execution",
        log_output
    )
    
    return eval_data


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


def _wait_for_ses_dkim_success(
        config: SelfDriverConfig,
        domain_name: str,
        poll_interval_seconds: int = 30,
        max_wait_minutes: int = 15
) -> None:
    """
    Wait for SES DKIM verification to reach SUCCESS for the given domain.

    This is robust to:
    - Region mismatches (defaults to us-west-2 if the config has no region)
    - SES API flavor mismatch (tries SESv2, then falls back to SESv1)
    - Transient API errors

    SUCCESS conditions (short-circuits the wait):
    - SESv2: DkimAttributes.Status == 'SUCCESS'
    - SESv1: GetIdentityDkimAttributes.DkimVerificationStatus == 'SUCCESS'
             OR GetIdentityVerificationAttributes.VerificationStatus == 'SUCCESS' (fallback)

    Raises AgentBlocked with context on timeout or terminal DKIM failure.
    """
    poll_interval_seconds = max(int(poll_interval_seconds or 5), 5)
    region = (config.env_type.get_aws_region() or "us-west-2").strip()
    
    config.log(
        f"Waiting for SES DKIM SUCCESS for {domain_name} in region {region} "
        f"(poll={poll_interval_seconds}s, timeout={max_wait_minutes}m)."
    )
    
    deadline = time.time() + (max_wait_minutes * 60)
    last_status = None
    
    while True:
        if time.time() > deadline:
            raise AgentBlocked({
                "desc": f"Timed out waiting for SES DKIM verification to reach SUCCESS for {domain_name}",
                "dkim_status": last_status or "UNKNOWN",
                "domain": domain_name,
                "region": region
            })
        
        status = None
        signing_enabled = None
        tokens = []
        
        # ---- Try SESv2 first
        try:
            sesv2 = boto3.client("sesv2", region_name=region)
            v2_resp = sesv2.get_email_identity(EmailIdentity=domain_name)
            dkim_attrs = v2_resp.get("DkimAttributes") or {}
            status = (dkim_attrs.get("Status") or "").upper()
            tokens = dkim_attrs.get("Tokens") or []
            signing_enabled = dkim_attrs.get("SigningEnabled")
        except Exception as exc:
            # Normalize NotFound across possible client shapes; fall through to v1.
            msg = str(exc)
            if "NotFound" in msg or "NotFoundException" in msg:
                status = "NOT_FOUND_V2"
            else:
                config.log(f"SESv2 get_email_identity error for {domain_name} in {region}: {exc}")
        
        # ---- Fall back to SESv1 if v2 could not confirm a SUCCESS
        if status in (None, "", "NOT_FOUND_V2"):
            try:
                sesv1 = boto3.client("ses", region_name=region)
                
                # Identity existence and basic verification status
                v1_ver = sesv1.get_identity_verification_attributes(
                    Identities=[domain_name]
                ).get("VerificationAttributes", {})
                ver_status = ((v1_ver.get(domain_name) or {}).get("VerificationStatus") or "").upper()
                
                # DKIM-specific status and tokens
                v1_dkim = sesv1.get_identity_dkim_attributes(
                    Identities=[domain_name]
                ).get("DkimAttributes", {})
                dkim_attrs = (v1_dkim.get(domain_name) or {})
                dkim_status = (dkim_attrs.get("DkimVerificationStatus") or "").upper()
                tokens = dkim_attrs.get("DkimTokens") or tokens
                
                # Prefer DKIM status; fall back to verification status if DKIM is unavailable
                status = dkim_status or ver_status or "NOT_FOUND"
            except Exception as exc:
                config.log(f"SESv1 fallback error for {domain_name} in {region}: {exc}")
                # Keep status as-is; we'll loop again.
        
        # Log on status change
        if status != last_status:
            token_preview = ", ".join(tokens) if tokens else "<no tokens>"
            config.log(
                f"SES DKIM wait status for {domain_name}: {status or 'UNKNOWN'}; "
                f"SigningEnabled={signing_enabled}; Tokens={token_preview}"
            )
            last_status = status
        
        # Terminal conditions
        if status in {"SUCCESS", "VERIFIED"}:
            config.log(f"SES DKIM verification succeeded for {domain_name} in {region}.")
            return
        
        if status in {"FAILED", "TEMPORARY_FAILURE"}:
            raise AgentBlocked({
                "desc": f"SES DKIM verification {status.lower()} for {domain_name}",
                "dkim_status": status,
                "domain": domain_name,
                "region": region,
                "dkim_tokens": tokens
            })
        
        time.sleep(poll_interval_seconds)


def build_opentofu_plan_context_messages(config: SelfDriverConfig, title=None) -> list[LlmMessage]:
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
        config: SelfDriverConfig,
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
        ## Architecture
        {initiative.architecture or initiative.business.architecture}
        """),
        textwrap.dedent(f"""
        ## User Documentation / Help Docs (**note** domain names in the docs may reference the top level business domain.  task level domains are usually subdomains - unless you're pushing to production env, use the subdomain)
        {initiative.user_documentation}
        """) if initiative.user_documentation else None
    ]


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
        )
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
                        code_writing_model=LlmModel.valid_or(cfi.get("code_writing_model"), LlmModel.OPENAI_GPT_5),
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


def write_initiative_tdd_test(config: SelfDriverConfig):
    config.iterate_if_necessary()
    
    task = config.task
    initiative = config.initiative
    
    test_file_path = write_test(
        config,
        description="Write initiative end-to-end test",
        test_file_name=f"test_initiative_{config.initiative.id}.py",
        system_prompt_name="codewriter--python_tdd_initiative.md",
        user_messages=[
            *LlmMessage.user_from_data(
                "Please one-shot write a single file, comprensive test suite that asserts the following Initiative has been implemented correctly",
                {
                    "name": initiative.title,
                    "description": initiative.description
                }
            ),
            *LlmMessage.user_from_data(
                "**Additional Context** = existing code and automated tests",
                [
                    f.get_latest_version().get_llm_message_data()
                    for f in config.business.codefile_set.all() if f.get_latest_version()
                ], item_name="file"
            )
        ]
    )
    
    with transaction.atomic():
        SelfDrivingTask.objects.filter(id=config.self_driving_task.id).update(
            test_file_path=test_file_path
        )
        config.self_driving_task.refresh_from_db(fields=["test_file_path"])


def write_task_tdd_test(config: SelfDriverConfig):
    config.iterate_if_necessary()
    task = config.task
    
    goal_data = {
        "GOAL": task.description,
        "acceptance_criteria": task.completion_criteria,
        "risk_notes": task.risk_notes,
    }
    if task.debug_steps:
        goal_data['debug_steps'] = task.debug_steps
    
    test_file_path = write_test(
        config,
        description="Write initial test",
        test_file_name=f"test_{task.id}.py",
        system_prompt_name="codewriter--python_tdd_task.md",
        user_messages=LlmMessage.user_from_data(
            "**Please one-shot write a single file, comprensive test suite that asserts this behavior.  This test suite will be used for Test Driven Development**",
            goal_data
        )
    )
    
    with transaction.atomic():
        SelfDrivingTask.objects.filter(id=config.self_driving_task.id).update(
            test_file_path=test_file_path
        )
        config.self_driving_task.refresh_from_db(fields=["test_file_path"])


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
    
    code_version_map = {
        cv.code_file_id: cv
        for cv in CodeVersion.objects.filter(
            task_iteration__self_driving_task__task__initiative_id=initiative.id,
            code_file__file_path__startswith="test",
            code_file__file_path__endswith=".py"
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


def write_test(
        config: SelfDriverConfig,
        description: str,
        test_file_name: str,
        system_prompt_name: str,
        user_messages: list[LlmMessage]
):
    sanitized_test_file_name = common.sanitize_filename(test_file_name)
    
    test_file_path_dir = config.sandbox_root_dir / "core" / "tests"
    test_file_path_dir.mkdir(parents=True, exist_ok=True)
    (test_file_path_dir / "__init__.py").touch(exist_ok=True)
    test_file_path = test_file_path_dir / sanitized_test_file_name
    
    user_messages = [
        *get_architecture_docs(config.initiative),
        *get_existing_test_context_messages(
            config.initiative,
            test_file_path
        ),
        *common.ensure_list(user_messages)
    ]
    
    previous_exception = None
    for i in range(3):
        try:
            messages = [
                get_sys_prompt([
                    "codewriter--python_test.md",
                    system_prompt_name,
                    "codewriter--python_tdd_common.md",
                    "common--infrastructure_rules_tofu.md",
                    "codewriter--common.md",
                    "codewriter--lambda_coder.md",
                    "common--iam_role_tofu.md",
                    "common--agent_provided_functionality_tofu.md",
                    "common--domain_management_tofu.md",
                    "common--llm_chat.md",
                    "common--credentials_architecture_tofu.md",
                    "common--forbidden_actions_tofu.md",
                    "common--environment_variables_tofu.md"
                ], replacements=[
                    ("<env_vars>", get_env_var_names(config)),
                    ("<business_tag>", config.business.service_token)
                ]),
                user_messages
            ]
            
            if previous_exception:
                messages.append(f"""
    Your previous attempt at writing this code failed with this exception:
    {previous_exception}

    Please attempt to write the code again and avoid causing this error
                """)
            
            code = llm_chat(
                description,
                messages,
                tag_entity=config.current_iteration
            ).text
            
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
                            sanitized_test_file_name,
                            code,
                            code_compilation_error
                        )
            
            test_file_path.write_text(code)
            code_verson = config.current_iteration.get_code_version(test_file_path.relative_to(config.sandbox_root_dir))
            code_verson.code = code
            code_verson.save()
            code_verson.write_to_disk(config.sandbox_root_dir)
            
            return test_file_path.relative_to(config.sandbox_root_dir)
        except Exception as e:
            config.log(e)
            previous_exception = e
    
    raise previous_exception


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
            [
                get_sys_prompt(
                    [
                        "failure_router.md",
                        "common--iam_role_tofu.md",
                        "common--agent_provided_functionality_tofu.md",
                        "common--domain_management_tofu.md",
                        "common--forbidden_actions_tofu.md",
                        "common--credentials_architecture_tofu.md",
                        "common--environment_variables_tofu.md"
                    ],
                    replacements=[
                        ("<env_vars>", get_env_var_names(config)),
                        get_readonly_files_replacement(config)
                    ]
                ),
                get_guidance_msg(
                    config
                ),
                get_architecture_docs(
                    config.initiative
                ),
                get_previous_iteration_summaries_msg(
                    config
                ),
                iteration_to_modify.get_error_llm_msg(
                    f"Error observed why building, deploying, or executing the code are modifying (Iteration {iteration_to_modify.version_number})"
                ),
                get_tombstone_message(
                    config
                ),
                get_lessons_msg(
                    "Do not repeat these mistakes - before you respond, checklist each item to make sure you're not repeating it",
                    config
                ),
                get_dependencies_msg(
                    config,
                    for_planning=True
                ),
                "Please perform the routing analysis"
            ],
            tag_entity=config.current_iteration,
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
        InfrastructureStackType.FOUNDATION.get_template_name(),
        InfrastructureStackType.APPLICATION.get_template_name()
    ]
    
    planning_data = llm_chat(
        "Plan aws provisioning code changes",
        [
            get_sys_prompt(
                [
                    "codeplanner--aws_provisioning_tofu.md",
                    "common--general_coding_rules.md",
                    "common--agent_provided_functionality_tofu.md",
                    "codeplanner--common.md",
                    "common--llm_chat.md",
                    "common--iam_role_tofu.md",
                    "common--agent_provided_functionality_tofu.md",
                    "common--domain_management_tofu.md",
                    "common--forbidden_actions_tofu.md",
                    "common--environment_variables_tofu.md",
                    "common--infrastructure_rules_tofu.md",
                    "codewriter--lambda_coder.md",
                    "common--credentials_architecture_tofu.md"
                ], replacements=[
                    ("<business_tag>", config.business.service_token),
                    ("<credential_manager_existing_services>", credential_manager.get_existing_service_names_desc()),
                    ("<credential_manager_existing_service_schemas>", credential_manager.get_existing_service_schema_desc()),
                    ("<env_vars>", get_env_var_names(config)),
                    get_readonly_files_replacement(config)
                ]
            ),
            get_guidance_msg(
                config
            ),
            get_architecture_docs(
                config.initiative
            ),
            config.business.get_existing_required_credentials_llmm(),
            get_lessons_msg(
                "Do not repeat these mistakes - before you respond, checklist each item to make sure you're not repeating it",
                config
            ),
            build_opentofu_plan_context_messages(
                config
            ),
            build_previous_iteration_context_messages(
                config,
                title="Previous Iterations"
            ),
            get_relevant_code_files(
                config,
                context_files
            ),
            LlmMessage.user_from_data(
                "structured failure triage object",
                routing_json
            ),
            LlmMessage.user_from_data(
                "Strategic Unblocking Guidance",
                config.iteration_to_modify.strategic_unblocking_json
            ) if config.iteration_to_modify.strategic_unblocking_json else None,
            get_tasktype_specific_instructions(config),
            "Please produce a development plan that addresses this issue"
        ],
        tag_entity=config.current_iteration,
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


def get_readonly_files_replacement(config: SelfDriverConfig) -> tuple[str, str]:
    parts = []
    
    for f in config.self_driving_task.get_readonly_files():
        if f['alternatives']:
            parts.append(f"- `{f['path']}` — {f['description']}. If you believe a change is needed to {f['path']}, the change likely belongs in `{f['alternatives']}` instead")
        else:
            parts.append(f"- `{f['path']}` — {f['description']}")
    
    return "<read_only_files>", "\n".join(parts)


def plan_direct_fix_code_changes(config: SelfDriverConfig):
    current_iteration = config.current_iteration
    previous_iteration = config.previous_iteration
    iteration_to_modify = config.iteration_to_modify
    routing_json = current_iteration.routing_json
    
    planning_data = llm_chat(
        "Plan quick fix code changes",
        [
            get_sys_prompt([
                "codeplanner--quick_fix.md",
                "common--general_coding_rules.md",
                "common--agent_provided_functionality_tofu.md",
                "codeplanner--common.md",
                "common--llm_chat.md",
                "common--iam_role_tofu.md",
                "common--agent_provided_functionality_tofu.md",
                "common--domain_management_tofu.md",
                "common--forbidden_actions_tofu.md",
                "common--environment_variables_tofu.md",
                "common--credentials_architecture_tofu.md",
                "common--infrastructure_rules_tofu.md",
                "codewriter--lambda_coder.md"
            ], replacements=[
                ("<business_tag>", config.business.service_token),
                ("<credential_manager_existing_services>", credential_manager.get_existing_service_names_desc()),
                ("<env_vars>", get_env_var_names(config)),
                ("<credential_manager_existing_service_schemas>", credential_manager.get_existing_service_schema_desc()),
                get_readonly_files_replacement(config)
            ]),
            get_guidance_msg(
                config
            ),
            get_architecture_docs(
                config.initiative
            ),
            config.business.get_existing_required_credentials_llmm(),
            get_lessons_msg(
                "Do not repeat these mistakes - before you respond, checklist each item to make sure you're not repeating it",
                config
            ),
            build_opentofu_plan_context_messages(
                config
            ),
            build_previous_iteration_context_messages(
                config,
                title="structured error reports"
            ),
            get_relevant_code_files(
                config,
                routing_json.get("context_files", [])
            ),
            LlmMessage.user_from_data(
                "structured failure triage object",
                routing_json
            ),
            get_tasktype_specific_instructions(config),
            LlmMessage.user_from_data(
                "Strategic Unblocking Guidance",
                config.iteration_to_modify.strategic_unblocking_json
            ) if config.iteration_to_modify.strategic_unblocking_json else None,
            "Please produce a development plan that addresses this issue"
        ],
        tag_entity=config.current_iteration,
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


def plan_test_fixing_code_changes(config: SelfDriverConfig):
    current_iteration = config.current_iteration
    previous_iteration = config.previous_iteration
    iteration_to_modify = config.iteration_to_modify
    
    relevant_code_files = get_relevant_code_files(config)
    
    business = config.self_driving_task.business
    
    task = config.self_driving_task.task
    task_type = TaskType(task.task_type)
    
    system_prompt_files = [
        MAP_TASKTYPE_TO_PLANNING_PROMPT[task_type],
        "codeplanner--test_fixer.md",
        "common--general_coding_rules.md",
        "common--agent_provided_functionality_tofu.md",
        "codeplanner--common.md",
        "common--llm_chat.md",
        "common--iam_role_tofu.md",
        "common--agent_provided_functionality_tofu.md",
        "common--domain_management_tofu.md",
        "common--forbidden_actions_tofu.md",
        "common--credentials_architecture_tofu.md",
        "common--environment_variables_tofu.md",
        "common--infrastructure_rules_tofu.md",
        "codewriter--lambda_coder.md"
    ]
    
    messages = [
        get_sys_prompt(
            system_prompt_files,
            [
                ("<business_tag>", config.business.service_token),
                ("<credential_manager_existing_services>", credential_manager.get_existing_service_names_desc()),
                ("<credential_manager_existing_service_schemas>", credential_manager.get_existing_service_schema_desc()),
                ("<env_vars>", get_env_var_names(config)),
                ("<aws_tag>", str(business.service_token)),
                ("<db_name>", str(business.service_token)),
                ("<iam_role_name>", str(business.get_iam_role_name())),
                ("<artifacts_directory>", str(config.artifacts_dir)),
                ("<sandbox_dir>", str(config.sandbox_root_dir)),
                get_readonly_files_replacement(config)
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
        ),
        get_dependencies_msg(
            config,
            for_planning=True
        ),
        relevant_code_files,
        get_docs_msg(
            config
        ),
        get_file_structure_msg(
            config.sandbox_root_dir
        ) if not iteration_to_modify.has_error() else [],
        get_guidance_msg(
            config
        ),
        get_lessons_msg(
            "Do not repeat these mistakes - before you respond, checklist each item to make sure you're not repeating it",
            config
        ),
        get_tasktype_specific_instructions(config),
        LlmMessage.user_from_data(
            "Strategic Unblocking Guidance",
            config.iteration_to_modify.strategic_unblocking_json
        ) if config.iteration_to_modify.strategic_unblocking_json else None,
        textwrap.dedent(f"""
            One or more of the automated tests have regressed in the new environment

            Your GOAL is to **MAKE THE FAILING TESTS PASS**
        """)
    
    ]
    
    planning_data = llm_chat(
        "Plan code changes",
        messages,
        model=config.model_code_planning,
        tag_entity=config.current_iteration,
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


def get_tasktype_specific_instructions(config: SelfDriverConfig) -> str:
    if TaskType.INITIATIVE_VERIFICATION.eq(config.task_type):
        return textwrap.dedent(f"""
                The Initiative's end-to-end test is located at 
                {config.self_driving_task.test_file_path}
                
                This test must always assert end-to-end behavior using real services (never mocks)
                
                This test is the last line of QA before a production push
            """)
    else:
        return None


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
        "common--general_coding_rules.md",
        "common--agent_provided_functionality_tofu.md",
        "codeplanner--common.md",
        "common--llm_chat.md",
        "common--iam_role_tofu.md",
        "common--agent_provided_functionality_tofu.md",
        "common--domain_management_tofu.md",
        "common--forbidden_actions_tofu.md",
        "common--credentials_architecture_tofu.md",
        "common--environment_variables_tofu.md",
        "common--infrastructure_rules_tofu.md",
        "codewriter--lambda_coder.md"
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
        ),
        get_dependencies_msg(
            config,
            for_planning=True
        ),
        relevant_code_files,
        get_docs_msg(
            config
        ),
        get_file_structure_msg(
            config.sandbox_root_dir
        ) if not iteration_to_modify.has_error() else [],
        get_guidance_msg(
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
        get_tasktype_specific_instructions(config),
        get_goal_msg(config, "Please plan code changes that work towards achieving this GOAL")
    
    ]
    
    planning_data = llm_chat(
        "Plan code changes",
        messages,
        model=config.model_code_planning,
        tag_entity=config.current_iteration,
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
                "common--iam_role_tofu.md",
                "common--agent_provided_functionality_tofu.md",
                "common--domain_management_tofu.md",
                "common--credentials_architecture_tofu.md",
                "common--forbidden_actions_tofu.md"
            ],
            replacements=[
                ("<business_tag>", config.business.service_token),
                ("<included_dependencies>", "\n\t\t".join(code_file_data.get("dependencies", []))),
                ("<sandbox_dir>", str(config.sandbox_root_dir)),
                ("<env_vars>", get_env_var_names(config))
            ]
        ),
        get_architecture_docs(
            config.initiative
        ),
        build_previous_iteration_context_messages(
            config,
            title="previous iteration evaluations - learn from these past attempts. **you must not repeat these errors**"
        ),
        get_tombstone_message(
            config
        ),
        LlmMessage.sys(
            "## Forbidden Actions\n• You **MUST NEVER** wrap the code in Markdown-style code fences such as ```<filetype>. Output must be raw code syntax only."
        )
        if not code_file_name.endswith(".md") else []
    ]
    if code_file_name in {
        InfrastructureStackType.FOUNDATION.get_template_name(),
        InfrastructureStackType.APPLICATION.get_template_name()
    }:
        messages += build_opentofu_plan_context_messages(config)
    
    related_code_file_versions = []
    for cfp in code_file_data.get("related_code_file_paths", []):
        if not CodeFile.objects.filter(business=config.business, file_path=cfp).exists():
            config.log(f"ERROR: related_code_file_path {cfp} does not exist")
            continue
        
        if cfp == code_file_data.get("code_file_path"):
            config.log(f"ERROR: related_code_file_path {cfp} is the same as the file to be edited")
            continue
        
        version = CodeFile.get(
            business=config.business,
            relative_path=cfp
        ).get_version(
            current_iteration,
            default_to_latest=True
        )
        
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
    
    if 'lambda' not in str(code_file_path) and code_file_name.endswith(".py"):
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
        messages.append(f"suggested prompt to assist in fixing the issue:  {fix_prompt}")
    
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
        f"Write code for {code_file_name} {code_file_data.get('validator')}",
        messages,
        model=code_writing_model,
        tag_entity=config.current_iteration
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
                code,
                code_file_data.get("validator")
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
    
    return Exception("failed to write validated code")


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
    elif code_file_name in {
        InfrastructureStackType.FOUNDATION.get_template_name(),
        InfrastructureStackType.APPLICATION.get_template_name()
    }:
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


def validate_code(
        config: SelfDriverConfig,
        code_file_path: Path,
        code: str,
        validator=None
) -> str:
    code_file_name = code_file_path.name.lower()
    if validator == "jinja":
        try:
            from jinja2 import Environment
            Environment().parse(code)
        except Exception as e:
            raise CodeCompilationError(code, f"Jinja syntax error: {e}")
    elif validator == "django_template":
        from django.template import Template
        try:
            Template(code)
        except Exception as e:
            raise CodeCompilationError(code, f"Django template syntax error: {e}")
    elif code_file_name.endswith(".js"):
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
    test_errors = config.iteration_to_modify.get_unit_test_errors() if config.iteration_to_modify else []
    
    previous_logs = {}
    if config.iteration_to_modify:
        previous_logs = common.get(config.iteration_to_modify, ["cloudformation_logs", "stacks"]) or {}
    
    has_deployment_error = any(
        isinstance(stack_log, Mapping) and stack_log.get("error")
        for stack_log in previous_logs.values()
    )
    
    if has_deployment_error:
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
    
    d = {
        "PRIMARY_OBJECTIVE": "achieving this goal is the primary objective of this code iteration",
        "GOAL": goal
    }
    
    if test_errors:
        d["failing_tests"] = test_errors
        d["domain_name"] = textwrap.dedent(f"""
            Note:  the domain name is dynamic and may change between test runs.  
            - the current domain name is `{config.initiative.domain}`
            - previous test failures might reference a different domain. As the domain name is dynamic, this is expected and not a cause for concern
        """)
    
    return LlmMessage.user_from_data(description, d)


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
            InfrastructureStackType.APPLICATION.get_template_name(),
            InfrastructureStackType.FOUNDATION.get_template_name(),
            "core/views.py",
            "core/urls.py",
            "core/models.py",
            "requirements.txt"
        ]
        
        iteration_code_files = set()
        iteration_code_versions = []
        for f in common.filter_none(required_files):
            iteration_code_versions.append(
                CodeFile.get(
                    business=config.business,
                    relative_path=f
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
            [
                get_sys_prompt("codefinder.md"),
                LlmMessage.user(config.self_driving_task.task.get_work_desc())
            ],
            model=LlmModel.OPENAI_GPT_5_MINI,
            tag_entity=config.current_iteration,
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


def sync_stack_identity(
        config: SelfDriverConfig,
        container_env: dict,
        cloudformation_params: dict = None
):
    stack_foundation, stack_application = get_stacks(config)
    
    container_env["STACK_NAME"] = stack_application.stack_name
    container_env["STACK_IDENTIFIER"] = container_env["TASK_NAMESPACE"] = stack_application.stack_namespace_token
    container_env["FOUNDATION_STACK_NAME"] = stack_foundation.stack_name
    container_env["FOUNDATION_STACK_IDENTIFIER"] = stack_foundation.stack_namespace_token
    
    if cloudformation_params:
        cloudformation_params["FoundationStackIdentifier"] = stack_foundation.stack_namespace_token
        cloudformation_params["StackIdentifier"] = stack_application.stack_namespace_token


def get_stacks(config: SelfDriverConfig) -> tuple[InfrastructureStack, InfrastructureStack]:
    stack_application = InfrastructureStack.get(
        config.initiative,
        InfrastructureStackType.APPLICATION,
        config.env_type
    )
    
    stack_foundation = InfrastructureStack.get(
        config.initiative,
        InfrastructureStackType.FOUNDATION,
        config.env_type
    )
    
    return stack_foundation, stack_application


def check_ses_quota(config: SelfDriverConfig):
    ses_client = boto3.client("ses", region_name=config.env_type.get_aws_region())
    quota = ses_client.get_send_quota()
    sent = float(quota.get("SentLast24Hours", 0))
    max_send = float(quota.get("Max24HourSend", 0))
    if max_send > 0 and sent >= max_send:
        raise AgentBlocked(
            json.dumps({
                "description": "SES send quota exhausted",
                "sent_last_24_hours": sent,
                "max_24_hour_send": max_send,
                "hint": "Request SES production access or switch to SES simulator addresses to continue sending email."
            }, indent=4)
        )


def manage_ses_domain_settings(
        config: SelfDriverConfig,
        tfvars_payload: Mapping[str, Any]
):
    domain_name = str(tfvars_payload.get("DomainName") or "").strip()
    if not domain_name:
        return
    
    check_ses_quota(config)
    _wait_for_ses_dkim_success(config, domain_name)


def deploy_opentofu_stack(
        config: SelfDriverConfig,
        *,
        stack_type: InfrastructureStackType,
        container_env: dict,
        container_image_tag: str,
        lambda_datas: list | None = None,
        previous_stack_outputs: dict | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    stack = config.stacks[stack_type]
    opentofu_stack_manager = config.stack_managers[stack_type]
    
    web_container_image = aws_utils.get_full_image_uri(
        config.ecr_repo_name,
        container_image_tag,
        config.env_type.get_aws_region()
    )
    
    ecr_arn = aws_utils.get_ecr_arn(
        config.ecr_repo_name,
        container_image_tag,
        config.env_type.get_aws_region()
    )
    
    tfvars_payload = build_tfvars_payload(
        config,
        stack,
        container_env=container_env,
        web_container_image=web_container_image,
        ecr_arn=ecr_arn,
        lambda_datas=lambda_datas,
        previous_stack_outputs=previous_stack_outputs
    )
    
    stack.stack_configuration = common.assert_exists(config.sandbox_root_dir / InfrastructureStackType(stack.stack_type).get_opentofu_config()).read_text()
    stack.stack_vars = {k: v for k, v in tfvars_payload.items() if "password" not in k.lower()}
    stack.sandbox_root_dir = config.sandbox_root_dir
    stack.updated_timestamp = common.get_now()
    stack.save()
    
    plan_summary: Mapping[str, Any] | None = None
    
    try:
        plan_result = opentofu_stack_manager.plan()
        plan_summary = plan_result.extra.get("plan_change_summary")
        
        apply_result = opentofu_stack_manager.apply()
        outputs = apply_result.extra["outputs"]
        
        if opentofu_stack_manager.get_resource_definitions("aws_ses_domain_dkim"):
            manage_ses_domain_settings(config, tfvars_payload)
        
        log_payload = opentofu_log_utils.build_opentofu_log_payload(
            stack_type=stack_type.value,
            plan_summary=plan_summary,
            results=[result.to_dict() for result in opentofu_stack_manager.run_results],
            tfvars=tfvars_payload,
        )
        
        config.add_deployment_log(stack_type, log_payload)
        
        stack.resources = opentofu_stack_manager.get_resources()
        stack.save()
        
        return outputs
    except OpenTofuCommandError as exc:
        error_payload = {
            "message": str(exc),
            "stderr": exc.result.stderr,
            "stdout": exc.result.stdout,
            "command": " ".join(exc.result.command),
        }
        
        opentofu_stack_manager.record(
            opentofu_stack_manager.stage,
            exc.result
        )
        
        log_payload = opentofu_log_utils.build_opentofu_log_payload(
            stack_type=stack_type.value,
            plan_summary=plan_summary,
            results=[result.to_dict() for result in opentofu_stack_manager.run_results],
            tfvars=tfvars_payload,
            error=error_payload,
        )
        
        raise AgentBlocked(json.dumps({
            "description": f"OpenTofu command failed for {stack_type.value}",
            **error_payload,
        }, indent=4))


def build_tfvars_payload(
        config: SelfDriverConfig,
        stack: InfrastructureStack,
        *,
        container_env: dict,
        web_container_image: str | None,
        ecr_arn: str | None,
        lambda_datas: list | None,
        previous_stack_outputs: dict | None = None
) -> dict[str, Any]:
    stack_type = InfrastructureStackType(stack.stack_type)
    stack_variables = config.stack_managers[stack_type].get_stack_variables()
    
    forbidden_variables = {"DBName", "DBPassword"}
    forbidden_in_module = forbidden_variables.intersection(stack_variables.keys())
    if forbidden_in_module:
        raise BadPlan(
            json.dumps({
                "description": "Terraform module defines forbidden variables",
                "stack_type": stack_type.value,
                "forbidden_variables": sorted(forbidden_in_module),
                "hint": "Remove these variable declarations. Database credentials are sourced from Secrets Manager."
            }, indent=4),
            config.current_iteration.planning_json
        )
    
    stack_foundation, stack_application = get_stacks(config)
    env_type = config.env_type
    secrets_key = config.business.get_secrets_root_key(env_type)
    
    developer_cidr = common.get_ip_address()
    shared_vpc = aws_utils.get_shared_vpc()
    
    payload: dict[str, Any] = {
        "StackIdentifier": stack_application.stack_namespace_token,
        "FoundationStackIdentifier": stack_foundation.stack_namespace_token,
        "ClientIpForRemoteAccess": developer_cidr,
        "DeletePolicy": "Retain" if EnvironmentType.PRODUCTION.eq(env_type) else "Delete",
        "AWS_ACCOUNT_ID": settings.AWS_ACCOUNT_ID,
        **get_admin_credentials(
            env_type,
            secrets_key
        )
    }
    
    if previous_stack_outputs:
        payload.update({
            **previous_stack_outputs,
            "RdsSecretArn": previous_stack_outputs.get("RdsMasterSecretArn"),
            "RdsEndpointAddress": previous_stack_outputs.get("RdsInstanceEndpoint"),
            "RdsEndpointPort": previous_stack_outputs.get("RdsInstancePort"),
            "DatabaseName": previous_stack_outputs.get("RdsInstanceDBName"),
        })
    
    if ecr_arn:
        payload["ECRRepositoryArn"] = ecr_arn
    
    if web_container_image:
        payload["WebContainerImage"] = web_container_image
    
    business = config.business
    if business.web_container_cpu is not None:
        payload["WebContainerCpu"] = business.web_container_cpu
    if business.web_container_memory is not None:
        payload["WebContainerMemory"] = business.web_container_memory
    if business.web_desired_count is not None:
        payload["WebDesiredCount"] = business.web_desired_count
    
    domain_name = config.business.domain if EnvironmentType.PRODUCTION.eq(config.env_type) else config.initiative.domain
    if not domain_name:
        raise AgentBlocked(
            json.dumps({
                "description": "Initiative has no domain",
                "stack_type": stack_type.value
            }, indent=4)
        )
    
    payload["DomainName"] = domain_name
    payload["DomainHostedZoneId"] = config.business.route53_hosted_zone_id
    payload["AlbCertificateArn"] = domain_manager.find_certificate_arn(
        business.domain,
        config.env_type.get_aws_region()
    )
    
    payload["VpcId"] = shared_vpc.vpc_id
    if shared_vpc.cidr_block:
        payload.setdefault("VpcCidr", shared_vpc.cidr_block)
    if len(shared_vpc.public_subnet_ids) >= 2:
        payload["PublicSubnet1Id"] = shared_vpc.public_subnet_ids[0]
        payload["PublicSubnet2Id"] = shared_vpc.public_subnet_ids[1]
    if len(shared_vpc.private_subnet_ids) >= 2:
        payload["PrivateSubnet1Id"] = shared_vpc.private_subnet_ids[0]
        payload["PrivateSubnet2Id"] = shared_vpc.private_subnet_ids[1]
    payload["SecurityGroupId"] = aws_utils.SHARED_RDS_SECURITY_GROUP_ID
    
    for lambda_data in lambda_datas or []:
        param_name = lambda_data.get('s3_key_param')
        param_value = lambda_data.get('s3_key_name')
        if param_name and param_value:
            payload[param_name] = param_value
    
    planning_required_creds = config.business.required_credentials or {}
    missing_secret_params: list[str] = []
    for svc_spec in planning_required_creds.values():
        variable_name = (
                svc_spec.get("secret_arn_variable")
                or svc_spec.get("secret_arn_cfn_parameter")
        )
        envvar_name = svc_spec.get("secret_arn_env_var")
        if not variable_name:
            continue
        if variable_name not in stack_variables:
            continue
        arn_value = container_env.get(envvar_name) if envvar_name else None
        if arn_value:
            payload[variable_name] = arn_value
        else:
            missing_secret_params.append(variable_name)
    
    if missing_secret_params:
        raise BadPlan(json.dumps({
            "description": "Missing required secret ARN variables for Terraform module",
            "stack_type": stack_type.value,
            "missing_secret_variables": sorted(missing_secret_params),
            "available_env_vars": container_env,
            "hint": "Ensure credential_manager returned the ARN and that the module variable names align with business.required_credentials"
        }, indent=4), config.current_iteration.planning_json)
    
    provided_values = {
        key: value
        for key, value in payload.items()
        if value not in [None, ""]
    }
    
    module_variable_names = set(stack_variables.keys())
    if module_variable_names:
        missing_variables = OpenTofuStackManager.validate_required_variables(
            stack_variables,
            provided_values
        )
        if missing_variables:
            raise BadPlan(json.dumps({
                "description": "Terraform module declares required variables without defaults, but the agent has no value for them",
                "stack_type": stack_type.value,
                "missing_variables": sorted(missing_variables)
            }, indent=4), config.current_iteration.planning_json)
    
    return provided_values


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


def ecr_authenticate(config: SelfDriverConfig, dockerfile):
    base_img = ""
    try:
        with open(dockerfile) as f:
            pattern = r'FROM(?:\s+--platform=\$\w+)?\s+(\d+\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[^\s:]+:[^\s]+)'
            #   FROM 123456789012.dkr.ecr.region.amazonaws.com/repo:tag
            #   FROM --platform=$TARGETPLATFORM 123456789012.dkr.ecr.region.amazonaws.com/repo:tag
            for base_img in re.findall(pattern, f.read(), flags=re.IGNORECASE):
                ecr_login(config, base_img)
    except Exception as e:
        config.log(e)
        raise AgentBlocked({"desc": f"Unable to authenticate with ecr for {base_img}", "error": str(e)})


def ecr_login(config: SelfDriverConfig, ecr_repo_uri):
    region = parse_region_from_ecr_uri(ecr_repo_uri)
    cmd = f"aws ecr get-login-password --region {region} | podman login --username AWS --password-stdin {ecr_repo_uri.split('/')[0]}"
    print(cmd)
    subprocess.run(
        cmd,
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


def empty_stack_buckets(
        config: SelfDriverConfig, *,
        delete_bucket=True
):
    if not EnvironmentType.DEV.eq(config.env_type):
        return
    
    bucket_definitions = get_stack_buckets(config)
    if not bucket_definitions:
        return
    
    logging.info(f"Found {len(bucket_definitions)} S3 bucket(s) in stack, emptying before deletion...")
    
    for bucket_resource in bucket_definitions:
        empty_s3_bucket(bucket_resource["bucket_name"], delete_bucket=delete_bucket)
