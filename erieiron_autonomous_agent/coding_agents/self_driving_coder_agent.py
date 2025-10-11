import copy
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import time
import traceback
from collections import defaultdict
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from typing import Optional

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
from erieiron_autonomous_agent.coding_agents.self_driving_coder_config import SelfDriverConfig, CodeReviewException, TASK_DESC_CODE_WRITING, BadPlan, GoalAchieved, RetryableException, AgentBlocked, MAP_TASKTYPE_TO_PLANNING_PROMPT, SdaPhase, NeedPlan, ExecutionException, CloudFormationException, CloudFormationStackDeleting, LAMBDA_PACKAGES_BUCKET, ERIEIRON_PUBLIC_COMMON_VERSION, USE_CODEX
from erieiron_autonomous_agent.enums import TaskStatus
from erieiron_autonomous_agent.models import CodeVersion, CodeMethod, SelfDrivingTaskIteration, Task, SelfDrivingTask, CodeFile, AgentLesson, AgentTombstone, Initiative, LlmRequest
from erieiron_autonomous_agent.system_agent_llm_interface import llm_chat, get_sys_prompt
from erieiron_autonomous_agent.utils import codegen_utils
from erieiron_autonomous_agent.utils.codegen_utils import CodeCompilationError, get_codebert_embedding, validate_dockerfile
from erieiron_common import common, aws_utils, domain_manager
from erieiron_common.aws_utils import sanitize_aws_name, STACK_STATUS_NO_STACK
from erieiron_common.enums import LlmModel, PubSubMessageType, TaskType, TaskExecutionSchedule, AwsEnv, DevelopmentRoutingPath, LlmReasoningEffort, CredentialService, LlmVerbosity, LlmMessageType
from erieiron_common.llm_apis.llm_constants import MODEL_PRICE_USD_PER_MILLION_TOKENS
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_common.message_queue.pubsub_manager import PubSubManager

sentence_transformer_model = SentenceTransformer("all-MiniLM-L6-v2")


def execute(task_id: str, reset=False, code_now=False, plan_now=False):
    self_driving_task = bootstrap_selfdriving_agent(
        task_id,
        reset
    )
    
    if plan_now or code_now:
        config = SelfDriverConfig(self_driving_task)
        config.init_log()
        
        config.set_iteration(
            SelfDrivingTaskIteration.objects.filter(planning_json__isnull=False).order_by("-timestamp").first()
        )
        
        if not plan_now and config.current_iteration.planning_json:
            codex_exec(
                config,
                config.current_iteration.planning_json
            )
        else:
            plan_and_implement_code_changes(
                config
            )
        
        build_deploy_exec_iteration(
            SelfDriverConfig(self_driving_task)
        )
        return
    
    for i in range(20):
        config = SelfDriverConfig(self_driving_task)
        
        try:
            if config.budget and config.self_driving_task.get_cost() > config.budget:
                logging.info(f"Stopping - hit the max budget ${config.budget :.2f}")
                break
            
            if i == 0:
                config.iterate_if_necessary()
                most_recent_iteration = self_driving_task.get_most_recent_iteration()
                
                if most_recent_iteration:
                    # we've re-started an self driving task - just execute on the first time around
                    config.set_iteration(most_recent_iteration)
                else:
                    # we've started an self driving task - this is the first iteration
                    config.set_iteration(self_driving_task.iterate())
                
                SelfDrivingTaskIteration.objects.filter(id=config.current_iteration.id).update(
                    log_content_execution="",
                    evaluation_json=None
                )
                
                config.current_iteration.refresh_from_db(
                    fields=["log_content_execution", "evaluation_json"]
                )
            else:
                try:
                    config.set_iteration(self_driving_task.iterate())
                    
                    if config.iteration_to_modify and i > 0:
                        config.iteration_to_modify.write_to_disk()
                    
                    plan_and_implement_code_changes(config)
                
                finally:
                    config.reset_log()
            
            execution_exception = None
            try:
                build_deploy_exec_iteration(config)
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
                evaluate_iteration(
                    config,
                    execution_exception
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
            handle_agent_blocked(config, agent_blocked)
            logging.info(f"Stopping - Agent Blocked")
            break
        except GoalAchieved as goal_achieved:
            handle_goal_achieved(config)
            logging.info(f"Stopping - Goal Achieved")
            break
        except Exception as e:
            logging.exception(e)
            config.log(e)
            if config.self_driving_task.task_id:
                PubSubManager.publish(
                    PubSubMessageType.TASK_FAILED,
                    payload={
                        "task_id": config.self_driving_task.task_id,
                        "error": traceback.format_exc()
                    }
                )
            
            logging.info(f"Stopping - Unhandled Exception")
            break
        finally:
            config.cleanup_iteration()


def plan_and_implement_code_changes(config):
    planning_data = plan_code_changes(config)
    config.set_phase(SdaPhase.CODING)
    
    ## TODO - inspect the code changes planned in planning_data.  if any of them is a lambda, do not use codex
    
    if USE_CODEX:
        codex_exec(config, planning_data)
    else:
        do_coding(config, planning_data)


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
    if TaskType.CODING_ML.eq(config.task_type):
        from erieiron_autonomous_agent.coding_agents.ml_packager import package_ml_artifacts
        package_ml_artifacts(config)
    
    try:
        config.git.add_commit_push(
            f"task {config.task.id}: {config.task.description}"
        )
        
        config.git.cleanup()
        
        if not TaskType.PRODUCTION_DEPLOYMENT.eq(config.task_type):
            domain_manager.delete_subdomain(config.domain_name)
        
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
    
    write_task_tdd_test(config)
    return config


def codex_exec(config: SelfDriverConfig, planning_data: dict):
    """Execute the plan using the Codex CLI pipeline."""
    config.log("Starting Codex CLI planning/execution pipeline")
    
    iteration_to_modify = config.iteration_to_modify
    
    readonly_entries = get_readonly_files_paths(config)
    readonly_lines = []
    for entry in common.ensure_list(readonly_entries):
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
    
    plan_path = config.artifacts_dir / f"{config.current_iteration.id}_plan.json"
    plan_path.write_text(json.dumps(planning_data, indent=2, default=str), encoding="utf-8")
    
    reference_prompts = [
        "prompts/common--general_coding_rules.md",
        "prompts/common--infrastructure_rules.md",
        "prompts/common--credentials_architecture.md",
        "prompts/codewriter--common.md",
        "prompts/codewriter--python_coder.md",
        "prompts/codewriter--lambda_coder.md",
        "prompts/codewriter--aws_cloudformation_coder.md",
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
        """)
    ]
    
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
    
    prompt_path = config.artifacts_dir / f"{config.current_iteration.id}_codex_prompt.txt"
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
    
    stdout_path = config.artifacts_dir / f"{config.current_iteration.id}_codex_stdout.log"
    stderr_path = config.artifacts_dir / f"{config.current_iteration.id}_codex_stderr.log"
    last_message_path = config.artifacts_dir / f"{config.current_iteration.id}_codex_last_message.txt"
    
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
            codex_metadata["cloudformation_feedback"] = feedback_sections.copy()
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
            "CloudFormation validation failed after Codex execution; retrying Codex with feedback",
            str(validation_error)
        )
    else:
        raise ExecutionException("Codex CLI reached maximum validation attempts without resolving infrastructure.yaml errors.")
    
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


def get_guidance_msg(config: SelfDriverConfig):
    if not config.guidance:
        return []
    else:
        return LlmMessage.sys_from_data(
            "## Important Additional Guidance",
            {"guidance_instructions": config.guidance}
        )


def validate_infrastructure(config, normalized_changed):
    if "infrastructure.yaml" not in normalized_changed:
        return
    
    infrastructure_path = config.sandbox_root_dir / "infrastructure.yaml"
    validation_error = None
    try:
        template_body = infrastructure_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        validation_error = BadPlan(
            "`infrastructure.yaml` is missing after Codex execution; restore the file and ensure it is valid."
        )
    except OSError as read_exc:
        validation_error = BadPlan(
            f"Unable to read `infrastructure.yaml` after Codex execution: {read_exc}"
        )
    else:
        try:
            validate_cloudformation_template(
                config,
                template_body
            )
        except BadPlan as exc:
            validation_error = exc
    return validation_error


def validate_all_changed_files(config, normalized_changed, planning_data):
    """Validate all changed files using appropriate validators"""
    validation_errors = []
    
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
    route_to = route_code_changes(config)
    
    if route_to in [DevelopmentRoutingPath.DIRECT_FIX, DevelopmentRoutingPath.AWS_PROVISIONING_PLANNER] and config.guidance:
        config.log(f"rerouting dev path from {route_to} to {DevelopmentRoutingPath.ESCALATE_TO_PLANNER} as we have task level guidance")
        route_to = DevelopmentRoutingPath.ESCALATE_TO_PLANNER
    
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
    
    Task.objects.filter(id=task.id).update(
        status=TaskStatus.IN_PROGRESS
    )
    
    self_driving_task: SelfDrivingTask = task.create_self_driving_env(
        reset_code_dir=reset
    )
    
    if reset:
        self_driving_task.selfdrivingtaskiteration_set.all().delete()
    
    config = SelfDriverConfig(self_driving_task)
    is_production_deployment = config.task_type.eq(TaskType.PRODUCTION_DEPLOYMENT)
    config.set_phase(SdaPhase.INIT)
    self_driving_task.get_git().pull()
    
    create_task_subdomain(self_driving_task)
    config.iterate_if_necessary()
    
    if not config.business.codefile_set.exists():
        config.git.mk_venv()
        config.business.snapshot_code(
            config.current_iteration,
            include_erie_common=True
        )
    
    if not self_driving_task.selfdrivingtaskiteration_set.filter(evaluation_json__isnull=False).exists():
        if not is_production_deployment:
            run_existing_tests(config, self_driving_task)
        
        config.business.snapshot_code(
            config.current_iteration,
            include_erie_common=False
        )
    
    if TaskType.INITIATIVE_VERIFICATION.eq(config.task_type) and common.invalid_file(config.sandbox_root_dir, config.initiative.test_file_path):
        config.set_phase(SdaPhase.CODING)
        write_initiative_tdd_test(config)
    
    elif (
            config.task_type in [TaskType.CODING_APPLICATION, TaskType.DESIGN_WEB_APPLICATION]
            and not is_production_deployment
            and common.invalid_file(config.sandbox_root_dir, self_driving_task.test_file_path)
    ):
        config.set_phase(SdaPhase.CODING)
        write_task_tdd_test(config)
    
    return self_driving_task


@transaction.atomic
def create_task_subdomain(self_driving_task: SelfDrivingTask):
    if TaskType.PRODUCTION_DEPLOYMENT.eq(self_driving_task.task.task_type):
        return
    
    previous_domain = (self_driving_task.domain or "").rstrip('.').lower()
    
    try:
        desired_domain, _, _ = self_driving_task.task.get_domain_and_cert(AwsEnv.DEV)
        desired_domain = self_driving_task.namespace_domain_with_stack_identifier(
            desired_domain,
            AwsEnv.DEV
        )
    except Exception as exc:
        logging.exception("Failed to determine task domain for %s: %s", self_driving_task.task_id, exc)
        raise AgentBlocked(f"unable to determine a valid domain for task {self_driving_task.task_id}")
    
    normalized_desired = (desired_domain or "").rstrip('.').lower()
    if not normalized_desired:
        raise AgentBlocked(f"unable to compute a domain for task {self_driving_task.task_id}")
    
    if previous_domain and previous_domain != normalized_desired:
        domain_manager.delete_subdomain(previous_domain)
    
    if self_driving_task.domain != normalized_desired:
        self_driving_task.domain = normalized_desired
        self_driving_task.save(update_fields=["domain"])


def ensure_alb_alias_record(config: SelfDriverConfig) -> None:
    domain_name = (config.domain_name or "").rstrip('.')
    hosted_zone_id = config.hosted_zone_id
    
    if not domain_name or not hosted_zone_id:
        config.log(f"Skipping DNS alias update; missing domain ({domain_name}) or hosted zone id ({hosted_zone_id})")
        return
    
    stack_name = config.self_driving_task.get_cloudformation_stack_name(config.aws_env)
    region = config.aws_env.get_aws_region()
    cf_client = boto3.client("cloudformation", region_name=region)
    
    load_balancer_arn = None
    try:
        paginator = cf_client.get_paginator("list_stack_resources")
        for page in paginator.paginate(StackName=stack_name):
            for resource in page.get("StackResourceSummaries", []) or []:
                if resource.get("ResourceType") != "AWS::ElasticLoadBalancingV2::LoadBalancer":
                    continue
                status = resource.get("ResourceStatus", "")
                if status and status.endswith("DELETE_COMPLETE"):
                    continue
                load_balancer_arn = resource.get("PhysicalResourceId")
                if load_balancer_arn:
                    break
            if load_balancer_arn:
                break
    except Exception as exc:
        logging.warning("Failed to enumerate load balancers for stack %s: %s", stack_name, exc)
        return
    
    if not load_balancer_arn:
        config.log(f"No Application Load Balancer found in stack {stack_name}; skipping DNS alias update")
        return
    
    elbv2_client = boto3.client("elbv2", region_name=region)
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
    except Exception as exc:
        logging.exception("Failed to upsert Route53 alias for %s", domain_name)
        raise AgentBlocked(f"failed to configure Route53 alias for {domain_name}: {exc}")
    
    config.log(f"Ensured Route53 alias for {domain_name} targets {dns_name}")


def build_docker_image(
        config: SelfDriverConfig,
        docker_env: dict,
        docker_file: Path
) -> str:
    exec_docker_prune()
    
    current_iteration = config.current_iteration
    self_driving_task = current_iteration.self_driving_task
    
    docker_image_tag_parts = [
        self_driving_task.business.name,
        self_driving_task.id,
        current_iteration.version_number
    ]
    
    if config.current_iteration.evaluation_json:
        # we already deployed this iteration, force a new docker image tag to make sure cloudformation updates
        docker_image_tag_parts.append(str(time.time())[-5:])
    
    docker_image_tag = sanitize_aws_name(docker_image_tag_parts, max_length=128)
    
    config.log(f"\n\n\n\n======== Begining DOCKER Build for tag {docker_image_tag} ")
    
    docker_build_cmd = common.strings([
        "docker",
        "build",
        "--build-arg", f"ERIEIRON_PUBLIC_COMMON_SHA={ERIEIRON_PUBLIC_COMMON_VERSION}",
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
    
    env_flags = " ".join(build_env_flags(docker_env))
    config.log(f"""
=========================================================
if you want to debug the docker container, run this 

docker run --rm -it \
  -v {config.sandbox_root_dir}:/app \
  -w /app \
  {env_flags} \
  {docker_image_tag} \
  /bin/bash

=========================================================
        """)
    
    return docker_image_tag


def exec_docker_prune():
    try:
        subprocess.run(["docker", "system", "prune", "-f"], check=True)
    except Exception as e:
        logging.exception(e)
        raise AgentBlocked("unable to run docker prune - is docker running?")


def update_rds_env_vars(
        config: SelfDriverConfig,
        docker_env: dict
):
    stack_name = config.self_driving_task.get_cloudformation_stack_name(config.aws_env)
    if not stack_name:
        raise AgentBlocked("unable to determine stack name")
    
    region = get_aws_region()
    if not region:
        raise AgentBlocked("unable to determine regions")
    
    cf_client = aws_utils.client("cloudformation")
    try:
        stack_descriptions = cf_client.describe_stacks(StackName=stack_name).get("Stacks")
    except Exception as e:
        raise AgentBlocked(f"unable to describe stack {stack_name}: {e}")
    
    if not stack_descriptions:
        raise AgentBlocked(f"no stack descriptions found for {stack_name}")
    
    cloudformation_output_vars = {
        o.get("OutputKey"): o.get("OutputValue")
        for o in common.ensure_list(common.first(stack_descriptions).get("Outputs"))
    }
    
    rds_secret_arn = cloudformation_output_vars.get("RdsMasterSecretArn")
    if not rds_secret_arn:
        raise BadPlan(f"Cloudformation stack lacks an output variable named RdsMasterSecretArn")
    
    rds_credential_def = config.business.required_credentials.get(CredentialService.RDS.value)
    secret_arn_env_var = rds_credential_def.get("secret_arn_env_var")
    docker_env[secret_arn_env_var] = rds_secret_arn
    
    output_varname_to_envname = {
        "RdsInstanceDBName": "ERIEIRON_DB_NAME",
        "RdsInstancePort": "ERIEIRON_DB_PORT",
        "RdsInstanceEndpoint": "ERIEIRON_DB_HOST"
    }
    
    for output_var_name, env_name in output_varname_to_envname.items():
        output_var_value = cloudformation_output_vars.get(output_var_name)
        if not output_var_value:
            raise BadPlan(f"Cloudformation stack lacks an output variable value for the required output variable named '{output_var_name}'")
        docker_env[env_name] = output_var_value


def get_aws_region():
    return os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or settings.AWS_DEFAULT_REGION_NAME


def get_env_var_names(config: SelfDriverConfig) -> str:
    env = build_env(config)
    return ", ".join(env.keys())


def build_env(config: SelfDriverConfig) -> dict:
    aws_env = config.aws_env
    aws_region = aws_env.get_aws_region()
    
    aws_credentials = botocore.session.Session(
        profile=os.environ.get("AWS_PROFILE")
    ).get_credentials().get_frozen_credentials()
    
    env = {
        "DOMAIN_NAME": config.domain_name,
        "AWS_DEFAULT_REGION": settings.AWS_DEFAULT_REGION_NAME,
        "AWS_ACCOUNT_ID": settings.AWS_ACCOUNT_ID,
        "AWS_ACCESS_KEY_ID": aws_credentials.access_key,
        "AWS_SECRET_ACCESS_KEY": aws_credentials.secret_key,
        "AWS_SESSION_TOKEN": aws_credentials.token,
        "LLM_API_KEYS_SECRET_ARN": settings.LLM_API_KEYS_SECRET_ARN,
        "STACK_IDENTIFIER": config.self_driving_task.get_cloudformation_key_prefix(aws_env),
        "TASK_NAMESPACE": config.self_driving_task.get_cloudformation_stack_name(aws_env),
        "CLOUDFORMATION_STACK_NAME": config.self_driving_task.get_cloudformation_stack_name(aws_env),
        "DOCKER_BUILDKIT": "1",
        "PATH": os.getenv("PATH")
    }
    
    for credential_service_name, cred_def in config.business.required_credentials.items():
        if credential_service_name == CredentialService.RDS.value:
            # cloudformation and rds handle the rds secret - we update this as a special case later
            continue
        
        secret_arn_env_var = cred_def.get("secret_arn_env_var")
        secrent_arn = credential_manager.manage_credentials(
            config,
            aws_env,
            credential_service_name,
            cred_def
        )
        env[secret_arn_env_var] = secrent_arn
    
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


def deploy_lambda_packages(config: SelfDriverConfig, ) -> list[dict]:
    s3 = aws_utils.client("s3")
    
    lambda_datas = get_stack_lambdas(config)
    
    for lambda_data in lambda_datas:
        dependencies = lambda_data["dependencies"]
        code_file_path = lambda_data["code_file_path"]
        full_lambda_path = config.sandbox_root_dir / code_file_path
        
        if not full_lambda_path.exists():
            raise FileNotFoundError(f"Lambda source file not found: {full_lambda_path}")
        
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = Path(temp_dir)
            shutil.copy(full_lambda_path, temp_dir_path / full_lambda_path.name)
            
            if dependencies:
                req_file = temp_dir_path / "requirements.txt"
                req_file.write_text("\n".join(dependencies))
                
                subprocess.run([
                    "pip", "install", "-r", str(req_file), "-t", str(temp_dir_path)
                ], check=True)
            
            s3_key_name = aws_utils.sanitize_aws_name(common.safe_join([
                config.task.id,
                config.current_iteration.version_number
            ], "-"), 1000) + ".zip"
            
            logging.info(f"Packaging Lambda: {code_file_path} → {s3_key_name} with dependencies: {dependencies}")
            
            zip_path = temp_dir_path / s3_key_name
            shutil.make_archive(zip_path.with_suffix(""), 'zip', root_dir=temp_dir_path)
            
            s3.upload_file(
                str(zip_path),
                LAMBDA_PACKAGES_BUCKET,
                s3_key_name
            )
            lambda_data['s3_key_name'] = s3_key_name
    
    return lambda_datas


def get_stack_lambdas(config) -> list[dict]:
    lambdas = []
    
    resources = get_infrastructure_yaml_data(config).get("Resources", {})
    for resource_name, resource_config in resources.items():
        if resource_config.get("Type") != "AWS::Lambda::Function":
            continue
        
        metadata = resource_config.get("Metadata", {})
        source_file = metadata.get("SourceFile")
        if not source_file:
            continue
        
        if not isinstance(source_file, str):
            raise Exception(f"Bad Lambda {resource_name}: SourceFile is not a string — got {source_file}")
        candidate_path = Path(source_file)
        
        props = resource_config.get("Properties", {})
        code_block = props.get("Code", {})
        s3_key_ref = code_block.get("S3Key")
        if not s3_key_ref:
            continue
        
        if not isinstance(s3_key_ref, dict) or "Ref" not in s3_key_ref:
            raise Exception(f"Bad Lambda {resource_name}: S3Key is not a Ref — got {s3_key_ref}")
        
        if not (config.sandbox_root_dir / candidate_path).exists():
            raise BadPlan(f"Bad Lambda {resource_name} definition: file not found — expected at {candidate_path}")
        
        code_file_code = (config.sandbox_root_dir / candidate_path).read_text()
        match = re.search(r'^# LAMBDA_DEPENDENCIES: (\[.*?])', code_file_code, flags=re.MULTILINE)
        if match:
            try:
                dependencies = json.loads(match.group(1))
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid LAMBDA_DEPENDENCIES in {candidate_path}: {e}")
        else:
            dependencies = []
        
        lambdas.append({
            "lambda_name": resource_name,
            "dependencies": dependencies,
            "code_file_path": str(candidate_path),
            "s3_key_param": s3_key_ref["Ref"]
        })
    
    return lambdas


def get_infrastructure_yaml_data(config: SelfDriverConfig) -> dict:
    return agent_tools.parse_cloudformation_yaml(
        get_infrastructure_yaml_code(config)
    )


def get_infrastructure_yaml_codeversion(config):
    return CodeFile.get(
        config.business, "infrastructure.yaml"
    ).get_latest_version(
        config.self_driving_task
    )


def get_infrastructure_yaml_code(config):
    infrastructure_code_version = get_infrastructure_yaml_codeversion(config)
    
    return infrastructure_code_version.code if infrastructure_code_version else None


def push_image_to_ecr(
        config: SelfDriverConfig,
        docker_image_tag: str
):
    region = config.aws_env.get_aws_region()
    ecr_client = boto3.client("ecr", region_name=region)
    account_id = aws_utils.client("sts").get_caller_identity()["Account"]
    
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
        command_args: list[str],
        docker_env: dict,
        docker_image: str
) -> None:
    command_args = common.ensure_list(command_args)
    iteration = config.current_iteration
    selfdriving_task = iteration.self_driving_task
    
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{config.sandbox_root_dir}:/app",
        "-w", "/app",
        *build_env_flags(docker_env),
        docker_image,
        "python", "manage.py",
        *common.safe_strs(command_args)
    ]
    
    config.log("\n" + "=" * 50 + "\n")
    config.log(f"RUNNING {' '.join(cmd)} in {config.sandbox_root_dir}\n")
    config.log("=" * 50 + "\n")
    
    # Capture docker run start time
    process = subprocess.Popen(
        cmd,
        stdout=config.log_f,
        env=docker_env,
        stderr=subprocess.STDOUT,
        text=True
    )
    
    config.log(f"Docker {command_args[-1]} execution started with PID {process.pid}, iteration_id: {iteration.id}")
    
    # Wait for completion
    while process.poll() is None:
        time.sleep(2)
    
    return_code = process.returncode
    
    if return_code == 0:
        config.log(f"\n{command_args[-1]} execution completed with return code: {return_code}\n")
    else:
        raise ExecutionException(f"\n{command_args[-1]} execution completed with return code: {return_code}\n")


def extract_exception(config: SelfDriverConfig, log_content: str) -> str:
    return llm_chat(
        "Log Extraction",
        [
            get_sys_prompt("log_parser.md"),
            log_content
        ],
        model=LlmModel.OPENAI_GPT_5,
        tag_entity=config.current_iteration
    ).text


def build_deploy_exec_iteration(config: SelfDriverConfig) -> str:
    start_epoch = int(time.time())
    try:
        docker_env = build_env(config)
        
        start_epoch = int(time.time())
        docker_image_tag = build_iteration(
            config,
            docker_env
        )
        
        start_epoch = int(time.time())
        deploy_iteration(
            config,
            docker_env,
            docker_image_tag
        )
        
        start_epoch = int(time.time())
        execute_iteration(
            config,
            docker_env,
            docker_image_tag
        )
        
        config.log("Docker execution finished")
    
    except Exception as e:
        if not isinstance(e, CloudFormationException):
            config.log(common.get_stack_trace_as_string(e))
        
        if config.self_driving_task.cloudformation_stack_name:
            local_logs = config.get_log_content() or ""
            
            time.sleep(30)  # pause to let logging catch up
            failure_details = aws_utils.read_cloudformation_failures(
                config.aws_env.get_aws_region(),
                config.self_driving_task.cloudformation_stack_name,
                start_epoch,
                local_logs=local_logs
            )
            
            sections: list[str] = []
            if local_logs:
                sections.append(local_logs)
            
            if failure_details:
                sections.append("==== CloudFormation Failure Details ====")
                sections.append(failure_details)
            
            enriched_logs = common.safe_join(sections, "\n\n")
            log_payload = enriched_logs or failure_details or local_logs
            extracted_exception = extract_exception(config, log_payload)
            
            if log_payload:
                config.log(log_payload)
            
            config.log(extracted_exception)
    
    finally:
        config.business.snapshot_code(config.current_iteration, include_erie_common=False)
        exec_docker_prune()


def execute_iteration(
        config: SelfDriverConfig,
        docker_env: dict,
        docker_image_tag: str
):
    config.set_phase(SdaPhase.EXECUTION)
    
    task = config.task
    task_type = config.task_type
    self_driving_task = config.self_driving_task
    
    if TaskType.CODING_ML.eq(task_type):
        run_docker_command(
            config=config,
            docker_env=docker_env,
            command_args=self_driving_task.main_name,
            docker_image=docker_image_tag
        )
        config.log_f.flush()  # Ensure ML execution logs are visible to tailing thread
    elif task_type.eq(TaskType.PRODUCTION_DEPLOYMENT):
        config.log("Skipping automated test run for production deployment task")
    else:
        run_docker_command(
            config=config,
            docker_env=docker_env,
            command_args=["test", "--keepdb", "--noinput"],
            docker_image=docker_image_tag
        )
        
        if TaskType.TASK_EXECUTION.eq(task_type) and TaskExecutionSchedule.ONCE.eq(task.execution_schedule):
            task_io_dir = Path(config.sandbox_root_dir) / "task_io"
            task_io_dir.mkdir(parents=True, exist_ok=True)
            
            input_file = task_io_dir / f"{task.id}-input.json"
            common.write_json(input_file, task.get_upstream_outputs())
            
            output_file = task_io_dir / f"{task.id}-output.json"
            
            run_docker_command(
                config=config,
                docker_env=docker_env,
                command_args=[
                    self_driving_task.main_name,
                    "--input_file", input_file,
                    "--output_file", output_file
                ],
                docker_image=docker_image_tag
            )


def deploy_iteration(
        config: SelfDriverConfig,
        docker_env: dict,
        docker_image_tag: str
):
    config.set_phase(SdaPhase.DEPLOY)
    
    task = config.task
    lambda_datas = None
    try:
        lambda_datas = deploy_lambda_packages(config)
    except BadPlan as bpe:
        raise bpe
    except Exception as e:
        config.log(e)
        raise BadPlan(f"task {task.id} is failing to push lambdas to s3. {e}")
    
    try:
        full_image_uri, ecr_arn = push_image_to_ecr(
            config,
            docker_image_tag
        )
    except Exception as e:
        raise BadPlan(f"task {task.id} is failing to push {docker_image_tag} to ECR. {e}")
    
    try:
        empty_stack_buckets(config, delete=False)
    except Exception as e:
        config.log(e)
        raise AgentBlocked(f"unable to empty buckets for stack {config.self_driving_task.cloudformation_stack_name}")
    
    stack_name = prepare_stack_for_update(
        config,
        docker_env
    )
    
    deploy_cloudformation_stacks(
        config,
        stack_name,
        docker_env,
        full_image_uri,
        ecr_arn,
        lambda_datas
    )
    
    ensure_alb_alias_record(config)
    
    if CredentialService.RDS.value in config.business.required_credentials:
        # special handling for RDS
        update_rds_env_vars(
            config,
            docker_env
        )
    
    manage_db(
        config,
        docker_env,
        docker_image_tag
    )
    
    return full_image_uri, stack_name


def build_iteration(config, docker_env):
    config.set_phase(SdaPhase.BUILD)
    
    iteration = config.current_iteration
    task_execution = init_task_execution(iteration)
    docker_file = config.sandbox_root_dir / "Dockerfile"
    
    ecr_authenticate_for_dockerfile(
        config,
        docker_file
    )
    
    docker_image_tag = build_docker_image(
        config,
        docker_env,
        docker_file
    )
    
    SelfDrivingTaskIteration.objects.filter(id=iteration.id).update(
        docker_tag=docker_image_tag
    )
    iteration.refresh_from_db(fields=["docker_tag"])
    
    return docker_image_tag


def infrastructure_yaml_modified_this_iteration(
        config: SelfDriverConfig,
        infrastructure_code_file: Optional[CodeFile] = None
) -> bool:
    infrastructure_code_file = infrastructure_code_file or config.business.codefile_set.filter(
        file_path="infrastructure.yaml"
    ).first()
    if not infrastructure_code_file:
        return False
    
    iteration = config.current_iteration
    infrastructure_code_version = iteration.codeversion_set.filter(
        code_file=infrastructure_code_file
    ).order_by("created_at").last()
    
    return bool(infrastructure_code_version and infrastructure_code_version.get_diff())


def is_stack_modified(
        config: SelfDriverConfig,
        infrastructure_modified: Optional[bool] = None
):
    infrastructure_code_file = config.business.codefile_set.filter(file_path="infrastructure.yaml").first()
    if not infrastructure_code_file:
        # no infrastructure
        return False
    
    if infrastructure_modified is None:
        infrastructure_modified = infrastructure_yaml_modified_this_iteration(
            config,
            infrastructure_code_file
        )
    
    if infrastructure_modified:
        # infrastructure.yaml modified in this iteration, need to push
        return True
    
    iteration = config.current_iteration
    infrastructure_code_version = iteration.get_code_version(infrastructure_code_file)
    
    lambda_modified = False
    try:
        infra_data = get_infrastructure_yaml_data(config)
        resources = infra_data.get("Resources", {})
        
        lambda_source_files = set()
        for resource_name, resource_config in resources.items():
            if resource_config.get("Type") == "AWS::Lambda::Function":
                properties = resource_config.get("Properties", {})
                code_config = properties.get("Code", {})
                
                if "ZipFile" in code_config:
                    continue
                
                s3_bucket = code_config.get("S3Bucket")
                s3_key = code_config.get("S3Key")
                
                if s3_key and isinstance(s3_key, str):
                    if s3_key.endswith(".py"):
                        lambda_source_files.add(s3_key)
                    elif s3_key.endswith(".zip"):
                        lambda_source_files.add(s3_key.replace(".zip", ".py"))
        
        if lambda_source_files:
            lambda_modified = any(
                cv.get_diff() for cv in iteration.codeversion_set.filter(
                    code_file__file_path__in=lambda_source_files
                )
            )
    except Exception as e:
        config.log(f"Failed to parse infrastructure.yaml for lambda detection: {e}")
        lambda_modified = any(
            cv.get_diff() for cv in iteration.codeversion_set.filter(
                code_file__file_path__icontains="lambda"
            )
        )
    return lambda_modified


def is_stack_exists(config: SelfDriverConfig) -> bool:
    return get_stack_status(config) != STACK_STATUS_NO_STACK


def is_stack_operational(config: SelfDriverConfig) -> bool:
    stack_status = get_stack_status(config)
    return stack_status in ["CREATE_COMPLETE", "UPDATE_COMPLETE"]


def manage_db(
        config: SelfDriverConfig,
        docker_env: dict,
        docker_image_tag: str
):
    try:
        aws_region_name = docker_env["AWS_DEFAULT_REGION"]
        
        rds_secret = agent_tools.get_secret_json(
            docker_env["RDS_SECRET_ARN"],
            aws_region_name
        )
        
        run_docker_command(
            config=config,
            docker_env=docker_env,
            command_args="makemigrations",
            docker_image=docker_image_tag
        )
        
        run_docker_command(
            config=config,
            docker_env=docker_env,
            command_args="migrate",
            docker_image=docker_image_tag
        )
    except Exception as e:
        if "DuplicateDatabase" in str(e):
            raise AgentBlocked(e.__dict__)
        else:
            raise e


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
        model=LlmModel.OPENAI_GPT_5,
        tag_entity=config.current_iteration,
        reasoning_effort=LlmReasoningEffort.LOW
    ).json()
    
    if not test_reviewer_output.get("all_passed"):
        raise AgentBlocked({
            "description": "assert_tests_green failed",
            **test_reviewer_output
        })


def evaluate_iteration(
        config: SelfDriverConfig,
        exception: Exception
):
    config.set_phase(SdaPhase.EVALUATE)
    
    iteration: SelfDrivingTaskIteration = SelfDrivingTaskIteration.objects.get(id=config.current_iteration.id)
    
    if isinstance(exception, AgentBlocked):
        raise exception
    
    if isinstance(exception, NeedPlan):
        return
    
    log_output = config.log_path.read_text()
    
    if "no space left on device" in common.default_str(log_output).lower():
        subprocess.run(["docker", "system", "prune", "-a", "-f"], check=True)
        raise RetryableException(f"execution is failing with 'no space left on device'\n\n{log_output}.  I just pruned docker, so should be cleared up now.")
    
    if TaskType.PRODUCTION_DEPLOYMENT.eq(config.task_type):
        goal_achieved_critera = textwrap.dedent(f"""
            - Set `"goal_achieved": true` only if the logs contain no errors and the cloudformation stack CREATE or UPDATE completed successfully
            - This is a production deployment task, so no automated tests should have been run
         """)
    else:
        goal_achieved_critera = textwrap.dedent(f"""
            **If the test output shows "Ran 0 tests", set `"goal_achieved": false`.**  
            **If any tests were skipped, set 'goal_achieved': false. Goal can only be set to true if all tests ran successfully without skips and the acceptance criteria are fully covered by the test suite.**  
            - Set `"goal_achieved": true` only if the logs contain no errors, the task output clearly meets the stated GOAL, and test logs show that one or more tests were actually run.  
            - If any errors or incomplete behaviors are detected in the logs, set `"goal_achieved": false`.  
            - Base this determination only on the current logs—do not consider prior iterations.
        """)
    
    messages = ([
        get_sys_prompt([
            "iteration_summarizer.md",
            "common--iam_role.md",
            "common--domain_management.md",
            "common--forbidden_actions.md",
            "common--environment_variables.md"
        ], replacements=[
            ("<env_vars>", get_env_var_names(config)),
            ("<goal_achieved_critera>", goal_achieved_critera)
        ]),
        get_goal_msg(config, "Task Goal")
    ])
    
    aws_env = config.aws_env
    stack_status = get_stack_status(config)
    
    messages += LlmMessage.user_from_data(
        "Cloudformation Status",
        {
            "stack_status": stack_status,
            "allow_goal_achieved": "True" if stack_status in ["CREATE_COMPLETE", "UPDATE_COMPLETE", "DEPLOY_COMPLETE"] else "False - the cloudformation stack is not deployed.  You may not declare goal complete under any circumstances"
        }
    )
    
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
        messages,
        LlmModel.OPENAI_GPT_5_NANO,
        tag_entity=config.current_iteration,
        output_schema="iteration_summarizer.md.schema.json"
    ).json()
    
    if isinstance(exception, ExecutionException):
        # ExecutionExceptions generally correspond to infra/deployment/runtime issues,
        # but could also arise during test runs.
        if "test" in str(exception).lower():
            # Attach to test_errors array
            test_error_entry = {
                "summary": "ExecutionException during test run",
                "logs": str(exception)
            }
            existing = eval_data.get("test_errors") or []
            eval_data["test_errors"] = existing + [test_error_entry]
        else:
            # Default to single error object
            eval_data.setdefault("error", {})
            eval_data["error"].setdefault("summary", "ExecutionException during iteration")
            eval_data["error"]["logs"] = str(exception)
    
    with transaction.atomic():
        SelfDrivingTaskIteration.objects.filter(id=iteration.id).update(
            evaluation_json=eval_data
        )
    
    if common.parse_bool(eval_data.get("goal_achieved")):
        raise GoalAchieved(eval_data)
    
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
            "iteration_full_evaluation": prev_iter.evaluation_json,
        })
    
    selection_data = llm_chat(
        "Iteration Selector",
        [
            get_sys_prompt([
                "iteration_selector.md",
                "common--iam_role.md",
                "common--domain_management.md",
                "common--forbidden_actions.md",
                "common--environment_variables.md"
            ], replacements=[
                ("<env_vars>", get_env_var_names(config)),
            ]),
            get_goal_msg(config, "Task Goal"),
            get_previous_iteration_summaries_msg(
                config
            ),
            LlmMessage.user_from_data(
                f"**Recent Iteration Full Evaluations**",
                previous_iteration_evals,
                "iteration_full_evaluation"
            )
        ],
        LlmModel.OPENAI_GPT_5_MINI,
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


def prepare_stack_for_update(config: SelfDriverConfig, docker_env: dict):
    cf_client = boto3.client("cloudformation", region_name=config.aws_env.get_aws_region())
    initial_stack_name = stack_name = config.self_driving_task.get_cloudformation_stack_name(config.aws_env)
    
    for i in range(5):
        matching_stack = get_stack(stack_name, cf_client)
        if not matching_stack:
            return config.self_driving_task.get_cloudformation_stack_name(config.aws_env)
        
        try:
            status = matching_stack["StackStatus"]
            need_rotate = (
                # status.endswith("FAILED")
                # or ("ROLLBACK" in status or status.startswith("DELETE")) and not status.endswith("COMPLETE")
                    status.startswith("DELETE") and not status.endswith("COMPLETE")
            )
            
            if need_rotate:
                try:
                    new_stack_name = config.self_driving_task.rotate_cloudformation_stack_name(
                        config.aws_env,
                        status=status,
                        reason="Stack observed rolling back or deleting before deployment"
                    )
                except ValueError as exc:
                    config.log(
                        f"Stack {stack_name} reported status {status} but rotation is not permitted: {exc}"
                    )
                    raise CloudFormationException(
                        f"Stack {stack_name} reported status {status} and cannot be rotated"
                    ) from exc
                
                config.log(
                    f"Stack {stack_name} reported status {status}. Rotated to {new_stack_name} before deployment.\n"
                )
                return new_stack_name
            
            try:
                cloudformation_wait(config, cf_client, stack_name)
                return stack_name
            except CloudFormationStackDeleting as deleting_exc:
                config.log(
                    f"Stack {deleting_exc.stack_name} is deleting; switching to {deleting_exc.new_stack_name} during preparation."
                )
                return deleting_exc.new_stack_name
        except cf_client.exceptions.ClientError as e:
            if "does not exist" in str(e):
                ...
            else:
                config.log(traceback.format_exc())
                raise
    
    if stack_name != initial_stack_name:
        config.log(
            f"Rotated CloudFormation stack name from {initial_stack_name} to {stack_name} before deployment."
        )
    
    with transaction.atomic():
        SelfDrivingTask.objects.filter(id=config.self_driving_task.id).update(
            cloudformation_stack_name=stack_name
        )
    config.self_driving_task.refresh_from_db(fields=["cloudformation_stack_name"])
    return stack_name


def cloudformation_wait(
        config: SelfDriverConfig,
        cf_client,
        stack_name,
        timeout=45 * 60,
        poll_interval=10,
        throw_on_fail=False
):
    start_time = time.time()
    first_status = None
    previous_status = None
    
    while True:
        stack = get_stack(stack_name, cf_client)
        if not stack:
            return
        
        time.sleep(poll_interval)
        status = stack['StackStatus']
        if first_status is None:
            first_status = status
            config.log(f"Stack {stack_name} first_status is {first_status}")
        elif status != previous_status:
            config.log(f"Stack {stack_name} status changed from {previous_status} to {status}")
            if "ROLLBACK" in status:
                config.log(
                    f"Stack {stack_name} observed status {status}; exiting wait so the agent can react."
                )
                if throw_on_fail:
                    raise CloudFormationException(
                        f"Stack {stack_name} entered rollback while waiting: {status}"
                    )
                else:
                    return
        
        wait_time = time.time() - start_time
        if wait_time > timeout:
            raise CloudFormationException(f"Timeout waiting for stack {stack_name} to reach a terminal state. Last status: {status}")
        config.log(f"waiting on {stack_name}.  status: {status}. waiting {int(wait_time)}s out of a max wait of {timeout}s")
        
        if "COMPLETE" in status and status.endswith("IN_PROGRESS"):
            continue
        
        if throw_on_fail:
            if "ROLLBACK" in status:
                config.log(
                    f"Stack {stack_name} observed status {status}; exiting wait so the agent can react."
                )
                raise CloudFormationException(
                    f"Stack {stack_name} entered rollback while waiting: {status}"
                )
            if status.startswith("DELETE"):
                try:
                    new_stack_name = config.self_driving_task.rotate_cloudformation_stack_name(
                        config.aws_env,
                        status=status,
                        reason="Stack observed deleting during deployment wait"
                    )
                except ValueError as exc:
                    raise CloudFormationException(
                        f"Stack {stack_name} entered {status} but rotation is not permitted: {exc}"
                    ) from exc
                
                config.log(
                    f"Stack {stack_name} is deleting (status={status}). Pivoting to new stack name {new_stack_name} and exiting wait."
                )
                raise CloudFormationStackDeleting(
                    stack_name=stack_name,
                    status=status,
                    new_stack_name=new_stack_name
                )
        else:
            if status.startswith("DELETE") and status.endswith("_IN_PROGRESS"):
                try:
                    new_stack_name = config.self_driving_task.rotate_cloudformation_stack_name(
                        config.aws_env,
                        status=status,
                        reason="Stack observed deleting during wait"
                    )
                except ValueError as exc:
                    raise CloudFormationException(
                        f"Stack {stack_name} entered {status} but rotation is not permitted: {exc}"
                    ) from exc
                
                config.log(
                    f"Stack {stack_name} is deleting (status={status}). Pivoting to new stack name {new_stack_name}."
                )
                raise CloudFormationStackDeleting(
                    stack_name=stack_name,
                    status=status,
                    new_stack_name=new_stack_name
                )
        
        previous_status = status
        if not status.endswith("_IN_PROGRESS"):
            break
    
    if throw_on_fail:
        assert_cloudformation_stack_valid(stack_name, cf_client)


def get_stack_status(config: SelfDriverConfig) -> str:
    stack_name = config.self_driving_task.get_cloudformation_stack_name(config.aws_env)
    cf_client = boto3.client("cloudformation", region_name=config.aws_env.get_aws_region())
    
    try:
        return common.first(cf_client.describe_stacks(StackName=stack_name)['Stacks'])['StackStatus']
    except:
        return STACK_STATUS_NO_STACK


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


def _load_cloudformation_template(template_body: str) -> dict:
    class _CloudFormationLoader(yaml.SafeLoader):
        """Lightweight loader that treats CloudFormation intrinsics as plain mappings."""
    
    def _construct_cfn_tag(loader, tag_suffix, node):
        if isinstance(node, yaml.ScalarNode):
            value = loader.construct_scalar(node)
        elif isinstance(node, yaml.SequenceNode):
            value = loader.construct_sequence(node)
        elif isinstance(node, yaml.MappingNode):
            value = loader.construct_mapping(node)
        else:
            value = None
        return {tag_suffix: value}
    
    _CloudFormationLoader.add_multi_constructor('!', _construct_cfn_tag)
    
    try:
        loaded = yaml.load(template_body, Loader=_CloudFormationLoader)
    except yaml.YAMLError as exc:
        logging.warning("Unable to parse CloudFormation template for Route53 guardrail validation: %s", exc)
        return {}
    except Exception as exc:  # pragma: no cover - defensive fallback
        logging.warning("Unexpected error while parsing CloudFormation template for guardrail validation: %s", exc)
        return {}
    
    if isinstance(loaded, dict):
        return loaded
    return {}


def _flatten_cfn_expr(expr) -> str | None:
    if isinstance(expr, str):
        return expr
    if isinstance(expr, (int, float)):
        return str(expr)
    if isinstance(expr, dict) and len(expr) == 1:
        tag, value = next(iter(expr.items()))
        normalized_tag = tag.replace("Fn::", "")
        if normalized_tag == "Ref" and isinstance(value, str):
            return f"${{{value}}}"
        if normalized_tag == "Sub":
            if isinstance(value, str):
                return value
            if isinstance(value, list) and value:
                return _flatten_cfn_expr(value[0])
        if normalized_tag == "Join" and isinstance(value, list) and len(value) == 2:
            delimiter = _flatten_cfn_expr(value[0]) or ""
            sequence = []
            for item in value[1] or []:
                flattened = _flatten_cfn_expr(item)
                if flattened is None:
                    return None
                sequence.append(flattened)
            return delimiter.join(sequence)
        if normalized_tag == "GetAtt":
            if isinstance(value, list):
                parts = []
                for part in value:
                    flattened = _flatten_cfn_expr(part)
                    parts.append(flattened if flattened is not None else str(part))
                return ".".join(parts)
            if isinstance(value, str):
                return value
        return None
    if isinstance(expr, list):
        pieces = []
        for item in expr:
            flattened = _flatten_cfn_expr(item)
            if flattened is None:
                return None
            pieces.append(flattened)
        return "".join(pieces)
    return None


def _summarize_cfn_expr(expr) -> str:
    flattened = _flatten_cfn_expr(expr)
    if flattened is not None:
        return flattened
    if isinstance(expr, (dict, list)):
        try:
            return json.dumps(expr, default=str)
        except TypeError:
            return str(expr)
    return str(expr)


def _summarize_record_target(record_props: dict) -> str:
    alias_target = record_props.get("AliasTarget")
    if isinstance(alias_target, dict):
        dns_name = _summarize_cfn_expr(alias_target.get("DNSName"))
        hosted_zone = _summarize_cfn_expr(alias_target.get("HostedZoneId"))
        return f"AliasTarget(DNSName={dns_name}, HostedZoneId={hosted_zone})"
    
    resource_records = record_props.get("ResourceRecords")
    if isinstance(resource_records, list) and resource_records:
        values = [
            _summarize_cfn_expr(record)
            for record in resource_records
        ]
        return f"ResourceRecords[{', '.join(values)}]"
    
    return "no target"


def _is_domainname_apex(name_expr) -> bool:
    flattened = _flatten_cfn_expr(name_expr)
    if not flattened:
        return False
    
    normalized = flattened.strip()
    apex_candidates = {"${DomainName}", "${DomainName}.", "DomainName", "DomainName."}
    return normalized in apex_candidates


def _inspect_route53_record(identifier: str, record_props: dict, issues: list[str], alias_records: list[tuple[str, str, dict]]) -> bool:
    if not isinstance(record_props, dict):
        return False
    
    name_expr = record_props.get("Name")
    if not _is_domainname_apex(name_expr):
        return False
    
    record_type = str(record_props.get("Type", "")).upper()
    target_summary = _summarize_record_target(record_props)
    
    if record_type == "CNAME":
        issues.append(
            f"{identifier} creates a CNAME for {_summarize_cfn_expr(name_expr)} ({target_summary})."
        )
        return True
    
    if record_type in {"A", "AAAA"}:
        alias_target = record_props.get("AliasTarget")
        if not isinstance(alias_target, dict):
            issues.append(
                f"{identifier} sets {record_type} for {_summarize_cfn_expr(name_expr)} without an AliasTarget ({target_summary})."
            )
            return True
        
        alias_records.append((identifier, record_type, alias_target))
        
        dns_name = _summarize_cfn_expr(alias_target.get("DNSName"))
        hosted_zone = _summarize_cfn_expr(alias_target.get("HostedZoneId"))
        
        if dns_name and "DomainName" in dns_name:
            issues.append(
                f"{identifier} alias DNSName resolves to {dns_name}; point it at the Application Load Balancer DNS attribute instead."
            )
        
        if hosted_zone and hosted_zone in {"${DomainHostedZoneId}", "DomainHostedZoneId"}:
            issues.append(
                f"{identifier} alias HostedZoneId is {hosted_zone}; use the ALB CanonicalHostedZoneID attribute."
            )
        
        return True
    
    # Other record types (e.g., MX, TXT) for DomainName are allowed and do not require alias enforcement.
    return True


def _enforce_route53_alias_guardrail(template_body: str) -> None:
    template = _load_cloudformation_template(template_body)
    resources = template.get("Resources") if isinstance(template, dict) else None
    if not isinstance(resources, dict) or not resources:
        return
    
    issues: list[str] = []
    alias_records: list[tuple[str, str, dict]] = []
    domainname_records_present = False
    
    for logical_id, resource in resources.items():
        if not isinstance(resource, dict):
            continue
        
        resource_type = resource.get("Type")
        properties = resource.get("Properties") or {}
        
        if resource_type == "AWS::Route53::RecordSet":
            domainname_records_present |= _inspect_route53_record(logical_id, properties, issues, alias_records)
        elif resource_type == "AWS::Route53::RecordSetGroup":
            record_sets = properties.get("RecordSets") or []
            if isinstance(record_sets, list):
                for idx, record in enumerate(record_sets):
                    identifier = f"{logical_id}[{idx}]"
                    domainname_records_present |= _inspect_route53_record(identifier, record, issues, alias_records)
    
    if domainname_records_present and not any(record_type == "A" for _, record_type, _ in alias_records):
        issues.append("No `Type: A` alias record for `!Ref DomainName` was found. Create an AliasTarget entry pointing to the Application Load Balancer.")
    
    if issues:
        message_lines = [
            "Route53 guardrail violation: `DomainName` must use alias A/AAAA records that target the Application Load Balancer.",
            "Detected issues:",
            *[f" - {issue}" for issue in issues],
            "Update `infrastructure.yaml` so the Route53 records use `Type: A` (and optionally `AAAA`) with `AliasTarget.DNSName` and `AliasTarget.HostedZoneId` wired to the ALB attributes."
        ]
        raise BadPlan("\n".join(message_lines))


def _record_is_ses_mx(record_props: dict) -> bool:
    if not isinstance(record_props, dict):
        return False
    
    record_type = str(record_props.get("Type", "")).upper()
    if record_type != "MX":
        return False
    
    name_expr = record_props.get("Name")
    flattened_name = _flatten_cfn_expr(name_expr) or ""
    normalized_name = flattened_name.rstrip(".")
    if normalized_name not in {"${DomainName}", "DomainName"}:
        return False
    
    resource_records = record_props.get("ResourceRecords")
    if not isinstance(resource_records, list):
        return False
    
    for record in resource_records:
        flattened_record = _flatten_cfn_expr(record) or ""
        if "inbound-smtp." in flattened_record and ".amazonaws.com" in flattened_record:
            return True
    
    return False


def _template_defines_ses_mx(template_body: str) -> bool:
    template = _load_cloudformation_template(template_body)
    resources = template.get("Resources") if isinstance(template, dict) else None
    if not isinstance(resources, dict):
        return False
    
    for resource in resources.values():
        if not isinstance(resource, dict):
            continue
        
        resource_type = resource.get("Type")
        properties = resource.get("Properties") or {}
        
        if resource_type == "AWS::Route53::RecordSet" and _record_is_ses_mx(properties):
            return True
        
        if resource_type == "AWS::Route53::RecordSetGroup":
            record_sets = properties.get("RecordSets") or []
            if isinstance(record_sets, list):
                for record in record_sets:
                    if _record_is_ses_mx(record):
                        return True
    
    return False


def _wait_for_ses_dkim_success(
        config: SelfDriverConfig,
        domain_name: str,
        poll_interval_seconds: int = 30,
        max_wait_minutes: int = 15
) -> None:
    poll_interval_seconds = max(poll_interval_seconds, 5)
    region = config.aws_env.get_aws_region()
    ses_client = boto3.client("sesv2", region_name=region)
    
    config.log(
        f"SES MX record detected; waiting for DKIM verification SUCCESS for {domain_name} in region {region}."
    )
    
    deadline = time.time() + max_wait_minutes * 60
    last_status = None
    
    while True:
        if time.time() > deadline:
            raise AgentBlocked({
                "desc": f"Timed out waiting for SES DKIM verification to reach SUCCESS for {domain_name}",
                "dkim_status": last_status,
                "domain": domain_name,
                "region": region
            })
        
        try:
            response = ses_client.get_email_identity(EmailIdentity=domain_name)
        except ses_client.exceptions.NotFoundException:
            status = "NOT_FOUND"
            tokens = []
            signing_enabled = None
        except Exception as exc:
            config.log(f"Error fetching SES DKIM status for {domain_name}: {exc}")
            time.sleep(poll_interval_seconds)
            continue
        else:
            dkim_attrs = response.get("DkimAttributes") or {}
            status = (dkim_attrs.get("Status") or "").upper()
            tokens = dkim_attrs.get("Tokens") or []
            signing_enabled = dkim_attrs.get("SigningEnabled")
        
        if status != last_status:
            token_preview = ", ".join(tokens) if tokens else "<no tokens>"
            config.log(
                f"Waiting for SES DKIM SUCCESS for {domain_name}; current status: {status or 'UNKNOWN'}; "
                f"SigningEnabled={signing_enabled}; Tokens={token_preview}"
            )
            last_status = status
        
        if status == "SUCCESS":
            config.log(f"SES DKIM verification succeeded for {domain_name}.")
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


def validate_cloudformation_template(
        config: SelfDriverConfig,
        template_body: str
):
    cf_client = boto3.client("cloudformation", region_name=config.aws_env.get_aws_region())
    config.log("Validating CloudFormation template before deployment")
    try:
        cf_client.validate_template(TemplateBody=template_body)
    except cf_client.exceptions.ClientError as exc:
        config.log(f"CloudFormation template validation failed: {exc}")
        raise BadPlan(f"CloudFormation template validation failed: {exc}") from exc
    except Exception as exc:
        config.log(f"Unexpected error during CloudFormation template validation: {exc}")
        raise BadPlan(
            f"CloudFormation template validation encountered an unexpected error: {exc}"
        ) from exc
    
    _enforce_route53_alias_guardrail(template_body)


def push_cloudformation(
        config: SelfDriverConfig,
        stack_name: str,
        cfn_file: Path,
        param_list: list,
        template_body: Optional[str] = None
):
    start_time = time.time()
    cf_client = boto3.client("cloudformation", region_name=config.aws_env.get_aws_region())
    try:
        config.log(f"pushing {stack_name} to {config.aws_env.get_aws_region()} with {cfn_file}")
        template_body = template_body or cfn_file.read_text()
        
        if get_stack(stack_name, cf_client):
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
        
        # pprint.pprint(get_stack(stack_name, cf_client))
        
        # Update SelfDrivingTask.cloudformation_stack_id with the deployed stack's physical ID
        try:
            stack_desc = get_stack(stack_name, cf_client)
            stack_id = stack_desc.get("StackId") if stack_desc else None
            if stack_id:
                with transaction.atomic():
                    SelfDrivingTask.objects.filter(id=config.self_driving_task.id).update(
                        cloudformation_stack_id=stack_id
                    )
            else:
                config.log(f"Could not resolve StackId for {stack_name}")
        except Exception as e:
            config.log(f"Failed to persist cloudformation_stack_id for {stack_name}: {e}")
        
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
        if push_time_mins > 30:
            config.log(f"cloudformation deploy for {stack_name} took {push_time_mins} minutes, which is too long!  CODE PLANNER:  think of ways to make cloudformation updates faster\n\n\n\n")


def compute_cfn_resource_durations(config: SelfDriverConfig, cf_client, stack_name, deployment_start_datetime):
    try:
        events = cf_client.describe_stack_events(StackName=stack_name).get('StackEvents', [])
    except Exception:
        return []
    
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


def build_cloudformation_durations_context_messages(config: SelfDriverConfig, title=None) -> list[LlmMessage]:
    iteration_to_modify = config.iteration_to_modify
    if not (iteration_to_modify and iteration_to_modify.slowest_cloudformation_resources):
        return []
    
    return LlmMessage.user_from_data(
        "Slowest CloudFormation Resources",
        {
            "cloudformation_durations": iteration_to_modify.slowest_cloudformation_resources
        }
    )


def build_previous_iteration_context_messages(config: SelfDriverConfig, title=None) -> list[LlmMessage]:
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
    
    previous_iteration_summaries = [
        {
            "iteration_id": i.id,
            "iteration_timestamp": i.timestamp,
            "summary": i.evaluation_json.get("summary")
        } for i in all_iterations if i.evaluation_json
    ]
    
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
    
    messages += LlmMessage.user_from_data(
        "Previous Iteration Summaries",
        previous_iteration_summaries,
        "previous_iteration_summary"
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
        if iteration == previous_iteration and previous_iteration != iteration_to_modify:
            description = "This is the evalutation of the execution of a previous iteration of the code"
        elif iteration == iteration_to_modify:
            description = "We are rolling the code back to this iteration. This is the evalutation of the execution of iteration of the code we are rolling back to.  We will start our new changes from this code"
        else:
            description = "This is an evaluation previous iteration of the code.  It is not the previous iteration neither is it the iteration we are rolling back to"
        
        iteration_data = {
            "iteration_id": iteration.id,
            "iteration_version_number": iteration.version_number,
            "iteration_description": description,
            "evaluation": iteration.evaluation_json
        }
        
        if not iteration.has_error():
            iteration_data = {
                **iteration_data,
                "error_summary": "None.  No errors detected.  The code executed without error.",
            }
        
        messages.append(iteration_data)
    
    return LlmMessage.user_from_data(
        title,
        messages,
        item_name="previous_iteration_analyses"
    )


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
        get_architecture_docs(
            config
        ),
        get_tombstone_message(
            config
        ),
        get_file_structure_msg(
            config.sandbox_root_dir
        ) if not iteration_to_modify.has_error() else [],
        get_prev_attemp_summaries(
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

        # Test Plan
        {task.test_plan or 'none'}

        # Risk Notes
        {task.risk_notes or 'none'}
        ''',
        "Please perform the code review"
    ]
    
    code_review_data = llm_chat(
        "Perform Code Review",
        messages,
        LlmModel.OPENAI_GPT_5,
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


def get_lessons_msg(
        title: str,
        config,
        task_desc=None,
        all_lessons=True,
        exclude_invalid=True,
        skip=False
) -> list[LlmMessage]:
    if skip:
        return
    
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
        tag_entity=config.current_iteration,
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
            *get_architecture_docs(config),
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
        Initiative.objects.filter(id=config.initiative.id).update(
            test_file_path=test_file_path
        )
        config.initiative.refresh_from_db(fields=["test_file_path"])


def write_task_tdd_test(config: SelfDriverConfig):
    config.iterate_if_necessary()
    task = config.task
    
    test_file_path = write_test(
        config,
        description="Write initial test",
        test_file_name=f"test_{task.id}.py",
        system_prompt_name="codewriter--python_tdd_task.md",
        user_messages=LlmMessage.user_from_data(
            "**Please one-shot write a single file, comprensive test suite that asserts this behavior.  This test suite will be used for Test Driven Development**",
            {
                "GOAL": task.description,
                "test_plan": task.test_plan,
                "risk_notes": task.risk_notes
            }
        )
    )
    
    with transaction.atomic():
        SelfDrivingTask.objects.filter(id=config.self_driving_task.id).update(
            test_file_path=test_file_path
        )
        config.self_driving_task.refresh_from_db(fields=["test_file_path"])


def get_existing_test_context_messages(
        config: SelfDriverConfig,
        excluding_test_path: Path
) -> list[LlmMessage]:
    """Build context messages describing other automated tests to avoid duplication."""
    test_dir = excluding_test_path.parent
    if not test_dir.exists():
        return []
    
    existing_test_entries: list[dict[str, str]] = []
    for path in sorted(test_dir.rglob("test*.py")):
        if path.name == "__init__.py" or not path.is_file():
            continue
        
        if Path(path).absolute() == excluding_test_path.absolute():
            continue
        
        try:
            code = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        
        if not code.strip():
            continue
        
        existing_test_entries.append({
            "path": str(path.relative_to(config.sandbox_root_dir)),
            "code": code
        })
    
    if not existing_test_entries:
        return []
    
    max_files_to_embed = 8
    selected_entries = existing_test_entries[:max_files_to_embed]
    
    messages = LlmMessage.user_from_data(
        f"""
        Existing automated tests that must continue to pass and should not be duplicated.  
        New tests must complement the provided suites, avoid duplicating coverage, 
        and remain consistent so all tests can pass together.
        """,
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
    
    user_messages = common.ensure_list(user_messages) + get_existing_test_context_messages(
        config,
        test_file_path
    )
    
    previous_exception = None
    for i in range(3):
        try:
            messages = [
                get_sys_prompt([
                    "codewriter--python_test.md",
                    system_prompt_name,
                    "codewriter--python_tdd_common.md",
                    "common--infrastructure_rules.md",
                    "codewriter--common.md",
                    "codewriter--lambda_coder.md",
                    "common--iam_role.md",
                    "common--domain_management.md",
                    "common--llm_chat.md",
                    "common--credentials_architecture.md",
                    "common--forbidden_actions.md",
                    "common--environment_variables.md"
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
                LlmModel.OPENAI_GPT_5,
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
                            test_file_path,
                            code,
                            code_compilation_error
                        )
            
            test_file_path.write_text(code)
            code_verson = config.current_iteration.get_code_version(test_file_path)
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
                        "common--iam_role.md",
                        "common--domain_management.md",
                        "common--forbidden_actions.md",
                        "common--credentials_architecture.md",
                        "common--environment_variables.md"
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
                    config
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
                get_prev_attemp_summaries(
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
            LlmModel.OPENAI_GPT_5,
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
        "infrastructure.yaml"
    ]
    
    planning_data = llm_chat(
        "Plan aws provisioning code changes",
        [
            get_sys_prompt(
                [
                    "codeplanner--aws_provisioning.md",
                    "common--general_coding_rules.md",
                    "codeplanner--common.md",
                    "common--llm_chat.md",
                    "common--iam_role.md",
                    "common--domain_management.md",
                    "common--forbidden_actions.md",
                    "common--environment_variables.md",
                    "common--infrastructure_rules.md",
                    "codewriter--lambda_coder.md",
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
            get_guidance_msg(
                config
            ),
            get_architecture_docs(
                config
            ),
            config.business.get_existing_required_credentials_llmm(),
            get_prev_attemp_summaries(
                config
            ),
            get_lessons_msg(
                "Do not repeat these mistakes - before you respond, checklist each item to make sure you're not repeating it",
                config
            ),
            build_cloudformation_durations_context_messages(
                config
            ),
            build_previous_iteration_context_messages(
                config,
                title="structured error reports"
            ),
            get_relevant_code_files(
                config,
                context_files
            ),
            LlmMessage.user_from_data(
                "structured failure triage object",
                routing_json
            ),
            "Please produce a development plan that addresses this issue"
        ],
        config.model_code_planning,
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


def get_readonly_files_paths(config: SelfDriverConfig) -> list[str]:
    alternative_path = None
    
    if config.task_type == TaskType.INITIATIVE_VERIFICATION:
        alternative_path = config.initiative.test_file_path
    elif config.task_type == TaskType.CODING_APPLICATION:
        alternative_path = config.self_driving_task.test_file_path
    
    return config.business.get_readonly_files(alternative_path)


def get_readonly_files_replacement(config: SelfDriverConfig) -> tuple[str, str]:
    parts = []
    
    for f in get_readonly_files_paths(config):
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
                "codeplanner--common.md",
                "common--llm_chat.md",
                "common--iam_role.md",
                "common--domain_management.md",
                "common--forbidden_actions.md",
                "common--environment_variables.md",
                "common--credentials_architecture.md",
                "common--infrastructure_rules.md",
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
                config
            ),
            config.business.get_existing_required_credentials_llmm(),
            get_prev_attemp_summaries(
                config
            ),
            get_lessons_msg(
                "Do not repeat these mistakes - before you respond, checklist each item to make sure you're not repeating it",
                config
            ),
            build_cloudformation_durations_context_messages(
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
            "Please produce a development plan that addresses this issue"
        ],
        config.model_code_planning,
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
        "codeplanner--common.md",
        "common--llm_chat.md",
        "common--iam_role.md",
        "common--domain_management.md",
        "common--forbidden_actions.md",
        "common--credentials_architecture.md",
        "common--environment_variables.md",
        "common--infrastructure_rules.md",
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
            config
        ),
        config.business.get_existing_required_credentials_llmm(),
        get_budget_message(
            config
        ),
        build_cloudformation_durations_context_messages(
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
        get_prev_attemp_summaries(
            config
        ),
        get_lessons_msg(
            "Do not repeat these mistakes - before you respond, checklist each item to make sure you're not repeating it",
            config
        ),
        get_goal_msg(config, "Please plan code changes that work towards achieving this GOAL")
    
    ]
    
    planning_data = llm_chat(
        "Plan code changes",
        messages,
        config.model_code_planning,
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
                "common--iam_role.md",
                "common--domain_management.md",
                "common--credentials_architecture.md",
                "common--forbidden_actions.md"
            ],
            replacements=[
                ("<business_tag>", config.business.service_token),
                ("<included_dependencies>", "\n\t\t".join(code_file_data.get("dependencies", []))),
                ("<sandbox_dir>", str(config.sandbox_root_dir)),
                ("<env_vars>", get_env_var_names(config))
            ]
        ),
        get_architecture_docs(
            config
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
        code_writing_model,
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
    
    elif code_file_name == "infrastructure.yaml":
        try:
            data = yaml.load(code, Loader=yaml.BaseLoader)
            if not data.get("Parameters"):
                raise Exception(f"infrastructure.yaml lacks parameters")
        except Exception as e:
            raise CodeCompilationError(code, f"infrastructure.yaml parse error:\n{e}")
        
        try:
            validate_cloudformation_template(
                config,
                code
            )
        except Exception as exc:
            raise CodeCompilationError(code, textwrap.dedent(f"""
                ## CloudFormation Validation Feedback

                Validation of `infrastructure.yaml` failed with:
                {exc}

                Apply the error details above to correct `infrastructure.yaml`, then rerun the validation before completing the Codex execution.
                """))
    
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


def get_goal_msg(config, description):
    task = config.self_driving_task.task
    
    goal = textwrap.dedent(f"""
        ## YOUR GOAL
        Write code to implement and/or deploy this task:
        '''
        {task.description}
        '''
        
        ## Test Plan (FYI)
        {task.test_plan}
        
        ## Risk Notes (FYI)
        {task.risk_notes}
    """)
    
    test_errors = config.iteration_to_modify.get_unit_test_errors() if config.iteration_to_modify else []
    
    if is_stack_exists(config):
        if not is_stack_operational(config):
            goal = textwrap.dedent(f"""
                The previous iteration failed at the deployment stage.   

                **Application level code changes are FORBIDDEN at this point, and will be FORBIDDEN until the deployment is fixed**
                - Any application level code changes at this point would be purely speculative and not based on an execution feedback loop
                - You may only plan changes for environment /  infrastructure files (Dockerfile, cloudformation configs (infrastructure.yaml), requirements.txt, etc)

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
    
    return LlmMessage.user_from_data(description, d)


def get_cloudformation_file(config: SelfDriverConfig) -> Path:
    return common.assert_exists(config.sandbox_root_dir / "infrastructure.yaml")


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
            LlmModel.OPENAI_GPT_5_MINI,
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
        docker_env: dict,
        cloudformation_params: list[dict] | None = None
) -> tuple[str, str]:
    self_driving_task = config.self_driving_task
    
    self_driving_task.refresh_from_db()
    current_stack = self_driving_task.get_cloudformation_stack_name(config.aws_env)
    current_identifier = self_driving_task.get_cloudformation_key_prefix(config.aws_env)
    docker_env["STACK_IDENTIFIER"] = current_identifier
    docker_env["TASK_NAMESPACE"] = current_stack
    docker_env["CLOUDFORMATION_STACK_NAME"] = current_stack
    
    if cloudformation_params is not None:
        synced = False
        for param in cloudformation_params:
            if param.get("ParameterKey") == "StackIdentifier":
                param["ParameterValue"] = current_identifier
                synced = True
                break
        if not synced:
            cloudformation_params.append({
                "ParameterKey": "StackIdentifier",
                "ParameterValue": current_identifier
            })
    return current_stack, current_identifier


def deploy_cloudformation_stacks(
        config: SelfDriverConfig,
        stack_name: str,
        docker_env: dict,
        web_container_image: str,
        ecr_arn: str,
        lambda_datas: dict
):
    cf_client = boto3.client("cloudformation", region_name=config.aws_env.get_aws_region())
    
    self_driving_task = config.self_driving_task
    aws_env = config.aws_env
    
    cfn_file = get_cloudformation_file(config)
    config.log(f"\n\n\n\n======== Begining cloudformation deploy for {stack_name} ")
    
    start_time = time.time()
    
    cloudformation_params = get_stack_parameters(
        config,
        docker_env,
        cfn_file,
        web_container_image,
        ecr_arn,
        lambda_datas
    )
    
    sync_stack_identity(config, docker_env, cloudformation_params)
    
    config.log(f"creating cloudformatin stack with params:  {json.dumps(cloudformation_params, indent=4)}")
    
    validate_parameters(
        config,
        cloudformation_params
    )
    
    template_body = cfn_file.read_text()
    
    validate_cloudformation_template(
        config,
        template_body
    )
    
    push_cloudformation(
        config,
        stack_name,
        cfn_file,
        cloudformation_params,
        template_body=template_body
    )
    
    if _template_defines_ses_mx(template_body):
        domain_name_param = next(
            (
                param.get("ParameterValue")
                for param in cloudformation_params
                if isinstance(param, dict) and param.get("ParameterKey") == "DomainName"
            ),
            None
        )
        
        domain_name_value = (domain_name_param or "").strip()
        if domain_name_value:
            _wait_for_ses_dkim_success(config, domain_name_value)
        else:
            config.log(
                "Detected SES MX record in CloudFormation template, but no DomainName parameter value was provided; skipping DKIM wait."
            )
    
    config.log(f"======== COMPLETED cloudformation deploy for {stack_name}\n\n\n\n")
    
    return stack_name


def delete_wedged_cloudformation_stack(config, docker_env, stack_name):
    cf_client = boto3.client("cloudformation", region_name=config.aws_env.get_aws_region())
    max_delete_attempts = 3
    stack_cleanup_successful = False
    last_delete_error: Exception | None = None
    for attempt in range(1, max_delete_attempts + 1):
        try:
            config.log(
                f"Attempting to delete wedged CloudFormation stack {stack_name} "
                f"(attempt {attempt}/{max_delete_attempts})"
            )
            delete_target = stack_name
            cf_client.delete_stack(StackName=delete_target)
            try:
                cloudformation_wait(
                    config,
                    cf_client,
                    delete_target
                )
            except CloudFormationStackDeleting as deleting_exc:
                stack_cleanup_successful = True
                stack_name = deleting_exc.new_stack_name
                stack_name, _ = sync_stack_identity(config, docker_env)
                config.log(
                    f"CloudFormation stack {delete_target} is deleting; will proceed with new stack name {stack_name}."
                )
                break
            
            if not get_stack(delete_target, cf_client):
                stack_cleanup_successful = True
                config.log(f"Successfully deleted CloudFormation stack {delete_target} after validation failure.")
                stack_name, _ = sync_stack_identity(config, docker_env)
                break
            
            last_delete_error = CloudFormationException(
                f"Stack {delete_target} still exists after delete attempt {attempt}."
            )
            config.log(str(last_delete_error))
        
        except cf_client.exceptions.ClientError as delete_exc:
            last_delete_error = delete_exc
            message = str(delete_exc)
            if "does not exist" in message.lower():
                stack_cleanup_successful = True
                config.log(f"Stack {stack_name} already deleted or missing; continuing deployment.")
                break
            
            config.log(
                f"Attempt {attempt}/{max_delete_attempts} failed to delete stack {stack_name}: {message}"
            )
        
        except Exception as delete_exc:  # pragma: no cover - defensive cleanup
            last_delete_error = delete_exc
            config.log(
                f"Attempt {attempt}/{max_delete_attempts} raised while deleting stack {stack_name}: {delete_exc}"
            )
        
        if attempt < max_delete_attempts:
            time.sleep(5)
    if not stack_cleanup_successful:
        if last_delete_error:
            config.log(
                f"Unable to clean up CloudFormation stack {stack_name} after {max_delete_attempts} attempts: {last_delete_error}"
            )
        raise AgentBlocked(
            f"cloudformation stack {stack_name} in {config.aws_env.get_aws_region()} is wedged and cannot be autonomously fixed.  "
            "A Human needs to clean up manually"
        )
    return stack_name


def validate_parameters(
        config: SelfDriverConfig,
        cloudformation_params
):
    pass


def get_stack_parameters(
        config: SelfDriverConfig,
        docker_env: dict,
        cfn_file: Path,
        web_container_image: str,
        ecr_arn: str,
        lambda_datas: list
):
    """
    Build the CloudFormation Parameters array for this stack.
    - Pulls required parameters from the template
    - Injects standard params (StackIdentifier, container image, ECRRepositoryArn, AWS_ACCOUNT_ID)
    - Resolves the RDS Secret ARN from the provided envvar->ARN pairs and planner output
    """
    self_driving_task = config.self_driving_task
    aws_env = config.aws_env
    secrets_key = config.business.get_secrets_root_key(aws_env)
    
    required_parameters, parameters_metadata = extract_cloudformation_params(
        cfn_file
    )
    
    if "DBName" in required_parameters:
        raise BadPlan("infrastructure.yaml has a parameter named 'DBName'. **NEVER** add a parameter to infrastructure.yaml named 'DBName'.  Delete this parameter.  DB credentials are stored the secrets value fetched from the rds secret", config.current_iteration.planning_json)
    
    if "DBPassword" in required_parameters:
        raise BadPlan("infrastructure.yaml has a parameter named 'DBPassword'. **NEVER** add a parameter to infrastructure.yaml named 'DBPassword'.  Delete this parameter.  DB credentials are stored the secrets value fetched from the rds secret", config.current_iteration.planning_json)
    
    aws_region = aws_env.get_aws_region()
    
    developer_cidr = common.get_ip_address()
    shared_vpc = aws_utils.get_shared_vpc()
    
    known_params = {
        "StackIdentifier": self_driving_task.get_cloudformation_key_prefix(aws_env),
        "ClientIpForRemoteAccess": developer_cidr,
        "DeletePolicy": "Retain" if AwsEnv.PRODUCTION.eq(aws_env) else "Delete",
        "ECRRepositoryArn": ecr_arn,
        "AWS_ACCOUNT_ID": settings.AWS_ACCOUNT_ID,
        
        # Intentionally DO NOT include legacy username/password params; use ARN pattern instead
        **get_admin_credentials(
            aws_env,
            secrets_key
        )
    }
    
    if web_container_image:
        known_params["WebContainerImage"] = web_container_image
    
    business = config.business
    known_params["WebContainerCpu"] = business.web_container_cpu
    known_params["WebContainerMemory"] = business.web_container_memory
    known_params["WebDesiredCount"] = business.web_desired_count
    
    known_params["DomainName"] = config.domain_name
    known_params["DomainHostedZoneId"] = config.hosted_zone_id
    known_params["AlbCertificateArn"] = config.certificate_arn
    
    # Shared networking parameters
    known_params["VpcId"] = shared_vpc.vpc_id
    known_params.setdefault("VpcCidr", shared_vpc.cidr_block)
    if len(shared_vpc.public_subnet_ids) >= 2:
        known_params["PublicSubnet1Id"] = shared_vpc.public_subnet_ids[0]
        known_params["PublicSubnet2Id"] = shared_vpc.public_subnet_ids[1]
    if len(shared_vpc.private_subnet_ids) >= 2:
        known_params["PrivateSubnet1Id"] = shared_vpc.private_subnet_ids[0]
        known_params["PrivateSubnet2Id"] = shared_vpc.private_subnet_ids[1]
    known_params["SecurityGroupId"] = aws_utils.SHARED_RDS_SECURITY_GROUP_ID
    
    for lambda_data in lambda_datas or []:
        known_params[lambda_data['s3_key_param']] = lambda_data['s3_key_name']
    
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
    
    # Block legacy TaskRoleArn usage and compute missing required params (ignoring those with defaults or marked optional)
    if "TaskRoleArn" in required_parameters:
        raise BadPlan(
            json.dumps({
                "description": "Remove the TaskRoleArn parameter from infrastructure.yaml.",
                "remediation": "Create IAM roles inside the template as needed, prefixing each RoleName with the StackIdentifier parameter value and keeping names under 64 characters.",
                "file": cfn_file.name
            }, indent=4),
            config.current_iteration.planning_json
        )
    
    # Compute missing required params (ignoring those with defaults or marked optional)
    missing = set()
    for param in required_parameters:
        param_meta = parameters_metadata.get(param, {})
        description = str(param_meta.get("Description", "")).lower()
        has_default = "Default" in param_meta
        is_optional = "(optional)" in description or has_default
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
            "description": "Missing required secret ARN CloudFormation parameter(s).",
            "file": cfn_file.name,
            "missing_secret_params": missing_secret_params,
            "available_env_vars": docker_env,
            "message": "Ensure credential_manager returned ARNs for these secrets and that they were passed into get_stack_parameters(envvar_secretarn_list=...)"
        }, indent=4), config.current_iteration.planning_json)
    
    if missing:
        raise BadPlan(json.dumps({
            "description": "infrastructure.yaml specifies the following parameters, but agent unable to supply them.  Either the parameters are invalid (likely) or need a default (most likely- try this) or the agent needs to be modified (not likely)",
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
    
    for k in parameters_metadata:
        if k not in required_parameters and k in known_params:
            params.append({
                "ParameterKey": k,
                "ParameterValue": str(known_params.get(k, ""))
            })
    
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
                        "description": f"Parameter '{key}' failed AllowedPattern validation",
                        "parameter": key,
                        "provided_value": val,
                        "allowed_pattern": pattern,
                        "hint": "Pass a value that matches the regex or relax/remove AllowedPattern in infrastructure.yaml"
                    }, indent=4), config.current_iteration.planning_json)
            except re.error as rex:
                raise BadPlan(json.dumps({
                    "description": f"Invalid AllowedPattern regex for parameter '{key}' in infrastructure.yaml",
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
    cmd = f"aws ecr get-login-password --region {region} | docker login --username AWS --password-stdin {ecr_repo_uri}"
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


def delete_cloudformation_stack(
        config,
        aws_env: AwsEnv,
        block_while_waiting=True
):
    if not AwsEnv.DEV.eq(aws_env):
        raise Exception(f"cannot delete a non DEV stack")
    
    stack_name = config.self_driving_task.cloudformation_stack_name
    if not stack_name:
        return
    
    cf_client = aws_utils.client("cloudformation")
    existing = get_stack(stack_name, cf_client)
    if not existing:
        config.log(f"CloudFormation stack {stack_name} does not exist. Nothing to delete.")
        return
    
    empty_stack_buckets(config, delete=True)
    
    config.log(f"Deleting CloudFormation stack {stack_name} in {get_aws_region()}")
    cf_client.delete_stack(StackName=stack_name)
    
    # Wait until the stack is deleted or reaches a terminal state
    if block_while_waiting:
        try:
            cloudformation_wait(config, cf_client, stack_name)
        except CloudFormationStackDeleting as deleting_exc:
            config.log(
                f"Stack {deleting_exc.stack_name} is deleting; moving forward with new stack name {deleting_exc.new_stack_name}."
            )
        else:
            config.log(f"Delete request finished for stack {stack_name}")


def empty_stack_buckets(config: SelfDriverConfig, delete=True):
    if not AwsEnv.DEV.eq(config.aws_env):
        return
    
    stack_name = config.self_driving_task.cloudformation_stack_name
    
    cf_client = aws_utils.client("cloudformation")
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
    s3_client = aws_utils.client("s3")
    
    for bucket_resource in s3_buckets:
        bucket_name = bucket_resource.get('PhysicalResourceId')
        if not bucket_name:
            continue
        
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
            
            # Delete the bucket after emptying
            if delete:
                s3_client.delete_bucket(Bucket=bucket_name)
                config.log(f"Deleted S3 bucket: {bucket_name}")
        
        except Exception as e:
            config.log(f"S3 bucket {bucket_name} no longer exists ({e}), skipping...")


def run_existing_tests(config, self_driving_task):
    if config.task_type.eq(TaskType.PRODUCTION_DEPLOYMENT):
        config.log("Skipping automated test suite for production deployment task")
        return
    
    config.iterate_if_necessary()
    
    has_cloudformation = config.business.codefile_set.filter(
        file_path="infrastructure.yaml"
    ).exists()
    
    has_completed_tasks = config.business.selfdrivingtask_set.filter(
        test_file_path__isnull=False,
        task__status=TaskStatus.COMPLETE
    ).exists()
    
    if not (has_cloudformation and has_completed_tasks):
        # no need to run the tests
        return
    
    # make sure the tests run
    delete_cloudformation_stack(config, AwsEnv.DEV, block_while_waiting=False)
    build_deploy_exec_iteration(config)
    
    assert_tests_green(config)
