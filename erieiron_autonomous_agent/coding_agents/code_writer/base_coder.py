import copy
import json
import logging
import os
import subprocess
import textwrap
import time
import types
from abc import ABC, abstractmethod
from pathlib import Path
from subprocess import CompletedProcess
from typing import Dict, Tuple, List

from django.db import transaction

from erieiron_autonomous_agent.coding_agents import credential_manager
from erieiron_autonomous_agent.coding_agents.coding_agent_config import (
    CodingAgentConfig,
    TASK_DESC_CODE_WRITING
)
from erieiron_autonomous_agent.coding_agents.self_driving_coder_exceptions import BadPlan
from erieiron_autonomous_agent.models import (
    LlmRequest,
    SelfDrivingTaskIteration, CodeFile, AgentLesson,
)
from erieiron_autonomous_agent.utils.codegen_utils import CodeCompilationError
from erieiron_common import common
from erieiron_common.enums import LlmReasoningEffort, LlmVerbosity, LlmMessageType, LlmCreativity, EnvironmentType


def truncate_log(
        content: str,
        max_lines: int = 500,
        max_bytes: int = 50_000,
        keep_tail: bool = True,
) -> str:
    """
    Truncate log content to max_lines and max_bytes (whichever is hit first).

    Args:
        content: Raw log content
        max_lines: Maximum lines to keep
        max_bytes: Maximum bytes to keep (secondary cap for dense logs)
        keep_tail: If True, keep last N lines (default). If False, keep first N.

    Returns:
        Truncated content with indicator of how many lines were removed.
    """
    if not content:
        return ""
    
    lines = content.splitlines()
    original_line_count = len(lines)
    
    # First pass: line limit
    if len(lines) > max_lines:
        if keep_tail:
            lines = lines[-max_lines:]
        else:
            lines = lines[:max_lines]
    
    # Second pass: byte limit
    result_lines = []
    current_bytes = 0
    line_iter = reversed(lines) if keep_tail else iter(lines)
    
    for line in line_iter:
        line_bytes = len(line.encode()) + 1  # +1 for newline
        if current_bytes + line_bytes > max_bytes:
            break
        result_lines.append(line)
        current_bytes += line_bytes
    
    if keep_tail:
        result_lines.reverse()
    
    truncated_count = original_line_count - len(result_lines)
    result = "\n".join(result_lines)
    
    if truncated_count > 0:
        if keep_tail:
            return f"[truncated {truncated_count} lines]\n{result}"
        else:
            return f"{result}\n[truncated {truncated_count} lines]"
    
    return result


def generate_summary(error_summary: str, log_metadata: list[dict]) -> str:
    """
    Generate summary markdown with metadata table and reading suggestions.

    Args:
        error_summary: High-level error summary text
        log_metadata: List of dicts with filename, size_kb, lines, truncated keys

    Returns:
        Formatted markdown summary
    """
    table_rows = "\n".join(
        f"| {m['filename']} | {m['size_kb']}KB | {m['lines']} | {'yes' if m['truncated'] else 'no'} |"
        for m in log_metadata
    )
    
    return textwrap.dedent(f"""\
        # Previous Iteration Error Logs

        ## Summary
        {error_summary}

        ## Available Logs
        | File | Size | Lines | Truncated |
        |------|------|-------|-----------|
        {table_rows}

        ## Reading Order
        Start with this summary. If you need details:
        1. init.log - initialization problems
        2. coding.log - if issue is related to generating the code
        3. deployment.json - deployment issues
        4. execution.log - test failures and other runtime errors
        5. cloudwatch.json - AWS-level errors
    """)


class BaseCoder(ABC):
    """Abstract base class for coding implementations with common functionality."""
    
    def __init__(self, config: CodingAgentConfig):
        super().__init__()
        self.config = config
        self.planning_data = common.assert_not_empty(config.current_iteration.planning_json)
    
    @property
    @abstractmethod
    def coder_name(self) -> str:
        """Return the name of this coder (e.g., 'codex', 'claude', 'gemini')."""
        pass
    
    @property
    def input_as_process_input(self):
        return False
    
    @property
    @abstractmethod
    def default_llm_model(self):
        """Return the default LLM model for this coder."""
        pass
    
    @abstractmethod
    def build_command(self, prompt_path: Path, artifact_paths: Dict[str, Path]) -> List[str]:
        """Build the CLI command for this coder."""
        pass
    
    def execute_command(self, command: List[str], prompt_text: str) -> 'CompletedProcess':
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self.config.sandbox_root_dir),
            env=os.environ.copy(),
            bufsize=1,
            text=True
        )
        stdout, stderr = proc.communicate(input=prompt_text)
        
        return types.SimpleNamespace(
            stdout=stdout,
            stderr=stderr,
            returncode=proc.returncode,
            args=command
        )
    
    @abstractmethod
    def check_for_api_errors(self, result: 'CompletedProcess') -> None:
        """Check for API-specific errors and raise appropriate exceptions."""
        pass
    
    @abstractmethod
    def extract_usage_stats(self, stdout: str, stderr: str, metadata: dict) -> Dict:
        """Extract token/cost metrics from execution output."""
        pass
    
    def execute_coding(self) -> Tuple[List[Path], Dict]:
        """Execute coding and return (changed_paths, execution_metadata)."""
        initiative = self.config.initiative
        business = initiative.business
        self.config.current_iteration.codeversion_set.all().delete()
        self.config.log(f"Starting {self.coder_name.title()} CLI planning/execution pipeline")
        
        # Set up artifact paths
        artifact_paths = self._setup_artifact_paths()
        
        try:
            # Build common data structures
            readonly_entries, readonly_lines = self._build_readonly_entries()
            code_file_paths, code_file_summary_lines = self._build_code_file_entries()
            business_context = self._extract_business_context()
            
            self.write_plan(artifact_paths)
            
            prev_iteration = self.config.iteration_to_modify
            error_summary, error_logs = prev_iteration.get_error()
            
            # Create directory for split logs
            log_dir = artifact_paths["previous_iteration_logs"].parent / "previous_iteration"
            log_dir.mkdir(exist_ok=True)
            
            deployment_json = common.get(
                prev_iteration,
                ["log_content_deployment", "deployment_errors"],
                common.get(
                    prev_iteration,
                    ["log_content_deployment", "deployment_logs"]
                )
            )
            
            log_files = [
                ("cloudwatch.json", common.json_format_pretty(prev_iteration.log_content_cloudwatch)),
                ("deployment.json", common.json_format_pretty(deployment_json)),
                ("execution.log", prev_iteration.log_content_execution),
                ("coding.log", prev_iteration.log_content_coding),
                ("init.log", prev_iteration.log_content_init),
            ]
            
            log_metadata = []
            for filename, content in log_files:
                content = content or ""
                truncated = content  # truncate_log(content)
                filepath = log_dir / filename
                filepath.write_text(truncated)
                
                # Collect metadata for summary
                log_metadata.append({
                    "filename": filename,
                    "size_kb": round(len(truncated.encode()) / 1024, 1),
                    "lines": len(truncated.splitlines()),
                    "truncated": truncated != content,
                })
            
            # Write summary index file
            (log_dir / "summary.md").write_text(generate_summary(error_summary, log_metadata))
            
            # Update artifact_paths to point to summary
            artifact_paths["previous_iteration_logs"] = log_dir / "summary.md"
            
            artifact_paths["architecture"].write_text(textwrap.dedent(f"""
                {business.architecture}
                
                #Initiative Specific Architecture Notes:
                {initiative.architecture or 'none'}
                
                # Credential Schemas
                {common.json_format_pretty(credential_manager.CREDENTIAL_DEFINITIONS)}
            """))
            
            if business.ui_design_spec:
                artifact_paths["design_spec"].write_text(business.ui_design_spec)
            
            # Build prompt
            prompt_text = self._build_prompt(
                business_context,
                readonly_lines,
                code_file_summary_lines,
                artifact_paths
            )
            artifact_paths["prompt"].write_text(prompt_text, encoding="utf-8")
            
            # Update planning metadata
            augmented_plan = self._create_augmented_plan(artifact_paths, code_file_paths)
            self._update_planning_json(augmented_plan)
            
            # Build command
            command = self.build_command(artifact_paths["prompt"], artifact_paths)
            
            # Execute with validation loop
            changed_paths, metadata = self._execute_with_validation_loop(
                command,
                prompt_text,
                artifact_paths,
                readonly_entries,
                business_context
            )
            
            # Persist code versions
            persisted_code_files = self._persist_codex_code_versions(changed_paths)
            
            # Update final metadata
            metadata["persisted_code_files"] = persisted_code_files
            self._update_final_metadata(metadata)
            
            self.config.log(f"Stored code versions for {self.coder_name.title()}-modified files", persisted_code_files)
            self.config.git.add_files()
            
            return changed_paths, metadata
        
        finally:
            self._cleanup_artifacts(artifact_paths)
    
    def _setup_artifact_paths(self) -> Dict[str, Path]:
        """Set up artifact file paths."""
        iteration_id = self.config.current_iteration.id
        artifacts_dir = self.config.artifacts_dir
        
        paths = {
            "plan": artifacts_dir / f"{iteration_id}_plan.json",
            "architecture": artifacts_dir / f"{iteration_id}_architecture.md",
            "design_spec": artifacts_dir / f"{iteration_id}_design_spec.md",
            "previous_iteration_logs": artifacts_dir / f"{iteration_id}_prev_iteration_logs.log",
            "prompt": artifacts_dir / f"{iteration_id}_{self.coder_name}_prompt.txt",
            "stdout": artifacts_dir / f"{iteration_id}_{self.coder_name}_stdout.log",
            "stderr": artifacts_dir / f"{iteration_id}_{self.coder_name}_stderr.log",
        }
        
        # Add coder-specific paths
        if self.coder_name == "codex":
            paths["last_message"] = artifacts_dir / f"{iteration_id}_codex_last_message.txt"
        elif self.coder_name == "claude":
            paths["session"] = artifacts_dir / f"{iteration_id}_claude_session.json"
        
        return paths
    
    def _build_readonly_entries(self) -> Tuple[List[Dict], List[str]]:
        """Build read-only entries and formatted lines."""
        readonly_entries = self.config.self_driving_task.get_readonly_files()
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
        
        return readonly_entries, readonly_lines
    
    def _build_code_file_entries(self) -> Tuple[List[str], List[str]]:
        """Build code file entries and summary lines."""
        code_file_entries = common.ensure_list(self.planning_data.get("code_files"))
        code_file_paths = [
            entry.get("code_file_path")
            for entry in code_file_entries
            if isinstance(entry, dict) and entry.get("code_file_path")
        ]
        code_file_summary_lines = [f"- {path}" for path in code_file_paths]
        return code_file_paths, code_file_summary_lines
    
    def _extract_business_context(self) -> Dict:
        """Extract business, initiative, and task context."""
        return {
            "business": self.config.business,
            "initiative": self.config.initiative,
            "task": self.config.task
        }
    
    def _build_prompt(
            self,
            business_context: Dict,
            readonly_lines: List[str],
            code_file_summary_lines: List[str],
            artifact_paths: dict[str, Path]
    ) -> str:
        """Build the complete prompt for the coder."""
        business = business_context["business"]
        initiative = business_context["initiative"]
        task = business_context["task"]
        
        # Reference prompts (common across all coders)
        reference_prompts = {
            "prompts/common--general_coding_rules.md",
            "prompts/codewriter--common.md"
        }
        
        for rule_context in self.planning_data.get("required_rule_contexts"):
            if rule_context == "infrastructure_rules":
                reference_prompts.add("prompts/common--agent_provided_functionality_tofu.md")
                reference_prompts.add("prompts/common--infrastructure_rules_tofu.md")
                reference_prompts.add("prompts/common--credentials_architecture_tofu.md")
                reference_prompts.add("prompts/codewriter--aws_cloudformation_coder_tofu.md")
            elif rule_context == "lambda_rules":
                reference_prompts.add("prompts/codewriter--lambda_coder.md")
            elif rule_context == "python_rules":
                reference_prompts.add("prompts/codewriter--python_rules.md")
            elif rule_context == "javascript_rules":
                reference_prompts.add("prompts/codewriter--javascript_coder.md")
            elif rule_context == "sql_rules":
                reference_prompts.add("prompts/codewriter--sql_coder.md")
            elif rule_context == "django_rules":
                reference_prompts.add("prompts/codewriter--django_rules.md")
            elif rule_context == "test_rules":
                reference_prompts.add("prompts/codewriter--python_tdd_task.md")
            elif rule_context == "ui_rules":
                reference_prompts.add("prompts/codewriter--javascript_coder.md")
                reference_prompts.add("prompts/codewriter--css_coder.md")
            elif rule_context == "security_rules":
                reference_prompts.add("prompts/codewriter--security_rules.md")
            elif rule_context == "database_rules":
                reference_prompts.add("prompts/codewriter--database_rules.md")
            elif rule_context == "ses_email_rules":
                reference_prompts.add("prompts/codewriter--ses_rules.md")
            elif rule_context == "s3_storage_rules":
                reference_prompts.add("prompts/codewriter--s3_rules.md")
            elif rule_context == "sqs_queue_rules":
                reference_prompts.add("prompts/codewriter--sqs_rules.md")
            elif rule_context == "cognito_rules":
                reference_prompts.add("prompts/codewriter--cognito_rules.md")
            else:
                self.config.log(f"ERROR:  unhandled required_rule_contexts value {rule_context}")
        
        # Build prompt parts
        domain_name = self.config.business.domain if EnvironmentType.PRODUCTION.eq(self.config.env_type) else self.config.initiative.domain
        prompt_parts = [
            self._get_coder_intro(artifact_paths),
            textwrap.dedent(f"""
            ### Risk Notes
            {task.risk_notes or 'None provided.'}
            """),
            textwrap.dedent(f"""
            ## Business & Architecture Context
            Business Service Token: {business.service_token}
            Doman Name: {domain_name}
            Initiative ID: {initiative.id}
            """),
        ]
        
        # Additional guidance
        if self.config.guidance:
            prompt_parts.append(textwrap.dedent(f"""
            ## Important Additional Guidance
            {self.config.guidance}
            """))
        
        # Read-only paths
        if readonly_lines:
            prompt_parts.append(textwrap.dedent("""
            ## Read-only Paths - NEVER modify these
            """ + "\n".join(readonly_lines)))
        
        # Add reference prompts
        for path in reference_prompts:
            try:
                content = Path(path).read_text()
                prompt_parts.append(textwrap.dedent(f"""
                
                ### Reference: {path}
                {content}
                
                
                
                """))
            except FileNotFoundError:
                self.config.log(f"Warning: Reference prompt not found: {path}")
        
        # Add development plan and execution checklist
        prompt_parts.extend(self._get_final_prompt_sections(artifact_paths))
        
        return "\n\n".join(part.strip() for part in prompt_parts if part)
    
    def _get_coder_intro(self, artifact_paths: dict[str, Path]) -> str:
        return textwrap.dedent(f"""
        You are assisting Erie Iron's self-driving coding workflow using Gemini.
        Work within the repository at `{self.config.sandbox_root_dir}`
        **DEVELOPMENT PLAN**:  Follow the approved development plan saved at `{artifact_paths.get("plan")}`.  Your job is to implement the `implementation_directive`, using the `diagnostic_context` for reference and learning from the `relevant_lessons`
        **SYSTEM ARCHITECTURE**:  The system architecture document is located at `{artifact_paths.get("architecture")}`.  You changes **must** be aligned with this architecture
        **UI DESIGN SPEC**:  If you make any UI changes, **you must** comport the look and feel of the changes to the UI Design Spec saved at `{artifact_paths.get("design_spec")}`
        **PREVIOUS ITERATION LOGS**  Error logs from the previous iteration are located at `{artifact_paths.get("previous_iteration_logs")}`. Start with the summary file for an overview. Individual log files (cloudwatch.json, deployment.json, execution.log, evaluation.log, coding.log, init.log) are in the same directory if detailed investigation is needed.
        Consult the relevant engineering standards from the reference prompts.
        Do not commit or push changes; the orchestrator handles git commits.
        """)
    
    def _get_final_prompt_sections(self, artifact_paths: dict[str, Path]) -> List[str]:
        return [
            textwrap.dedent(f"""
            ## Execution Checklist
            1. Read and understand the full development plan at `{artifact_paths.get("plan")}`.  Your job is to implement the `implementation_directive`, using the `diagnostic_context` for reference and learning from the `relevant_lessons`
            2. Read and understand the system architecture at `{artifact_paths.get("architecture")}`.  Validate your changes comport to the architecture
            3. Error logs from the previous iteration are at `{artifact_paths.get("previous_iteration_logs")}`. Read the summary first; if needed, review individual log files in the same directory for detailed context.
            4. If you are making UI / look and feel changes, understand the UI Design Spec at `{artifact_paths.get("design_spec")}`.  Validate your changes comport to the design spec
            5. Apply all Erie Iron engineering standards from the reference prompts
            6. Implement code changes that satisfy the `implementation_directive` and address prior failures
            7. Scope modifications to the `implementation_directive`.  **Do not** make un-related changes
            8. Never modify read-only paths
            9. Leave repository with changes ready for review; do not commit
            """)
        ]
    
    def _create_augmented_plan(self, artifact_paths: Dict[str, Path], code_file_paths: List[str]) -> dict:
        """Create augmented plan with metadata."""
        augmented_plan = copy.deepcopy(self.planning_data)
        augmented_plan["paths"] = {
            "plan_path": str(artifact_paths["plan"]),
            "prompt_path": str(artifact_paths["prompt"]),
            "code_file_paths": code_file_paths,
        }
        return augmented_plan
    
    def _update_planning_json(self, new_planning_data: dict) -> None:
        self.planning_data = new_planning_data
        """Update the planning JSON in the database."""
        with transaction.atomic():
            SelfDrivingTaskIteration.objects.filter(id=self.config.current_iteration.id).update(
                planning_json=self.planning_data
            )
        self.config.current_iteration.refresh_from_db(fields=["planning_json"])
    
    def _execute_with_validation_loop(
            self,
            command: List[str],
            prompt_text: str,
            artifact_paths: Dict[str, Path],
            readonly_entries: List[Dict],
            business_context: Dict
    ) -> Tuple[List[Path], Dict]:
        """Execute command with validation feedback loop."""
        prior_file_checksum_map = self.get_file_checksum_map(self.config.sandbox_root_dir)
        feedback_sections: list[str] = []
        max_validation_attempts = 2
        attempt = 0
        changed_paths: list[Path] = []
        
        business = business_context["business"]
        initiative = business_context["initiative"]
        
        while attempt < max_validation_attempts:
            attempt += 1
            prompt_with_feedback = prompt_text
            if feedback_sections:
                prompt_with_feedback = prompt_with_feedback + "\n\n" + "\n\n".join(feedback_sections)
            artifact_paths["prompt"].write_text(prompt_with_feedback, encoding="utf-8")
            
            self.config.log(
                f"Running {self.coder_name.title()} CLI (attempt {attempt})",
                " ".join(command) if isinstance(command, list) else str(command),
                f"Prompt saved to {artifact_paths['prompt']}"
            )
            
            start_time = time.time()
            
            result = self.execute_command(
                command,
                prompt_with_feedback
            )
            
            # Save output
            artifact_paths["stdout"].write_text(result.stdout or "", encoding="utf-8")
            artifact_paths["stderr"].write_text(result.stderr or "", encoding="utf-8")
            
            # Check for API errors
            self.check_for_api_errors(result)
            
            # Extract usage stats
            usage_metrics = self.extract_usage_stats(
                result.stdout,
                result.stderr,
                {"config": self.config, **{k: v for k, v in artifact_paths.items() if k in ["last_message", "session"]}}
            )
            
            # Update metadata and create LLM request
            metadata = self._create_execution_metadata(
                artifact_paths,
                start_time,
                result,
                attempt,
                feedback_sections,
                usage_metrics,
                business_context
            )
            
            self.config.log(
                f"{self.coder_name.title()} CLI completed successfully",
                {
                    "stdout_path": str(artifact_paths["stdout"]),
                    "stderr_path": str(artifact_paths["stderr"]),
                    "attempt": attempt
                }
            )
            
            # Collect changed files
            changed_paths = self._collect_repo_changed_files(prior_file_checksum_map, readonly_entries)
            
            if not changed_paths:
                raise BadPlan(f"{self.coder_name.title()} CLI produced no persistable file changes")
            
            # Validate changes
            normalized_changed = {self._normalize_relative_path(p) for p in changed_paths}
            validation_error = None # self.validate_all_changed_files(normalized_changed)
            
            if validation_error is None:
                break
            
            if attempt >= max_validation_attempts:
                raise BadPlan(f"Codewriting failed with {validation_error}")
            
            # Extract lessons and add feedback
            from erieiron_autonomous_agent.coding_agents.coding_agent import extract_lessons
            extract_lessons(self.config, TASK_DESC_CODE_WRITING, validation_error)
            feedback_sections.append(
                textwrap.dedent(
                    f"""
                    Code validation failed with the following error:
                    {validation_error}

                    Apply the error details above to correct the problem
                    """
                ).strip()
            )
            self.config.log(
                f"OpenTofu validation failed after {self.coder_name.title()} execution; retrying with feedback",
                str(validation_error)
            )
        else:
            from erieiron_autonomous_agent.coding_agents.self_driving_coder_exceptions import ExecutionException
            raise ExecutionException(
                f"{self.coder_name.title()} CLI reached maximum validation attempts without resolving OpenTofu validation errors."
            )
        
        return changed_paths, metadata
    
    def _create_execution_metadata(
            self,
            artifact_paths: Dict[str, Path],
            start_time: float,
            result: 'CompletedProcess',
            attempt: int,
            feedback_sections: List[str],
            usage_metrics: Dict,
            business_context: Dict
    ) -> Dict:
        """Create execution metadata and LLM request."""
        business = business_context["business"]
        initiative = business_context["initiative"]
        
        # Extract metrics
        total_cost_usd = usage_metrics.get("total_cost_usd", 0)
        total_tokens = usage_metrics.get("total_tokens", 0)
        
        # Create LLM request
        LlmRequest.objects.create(
            title=self.coder_name.title(),
            reasoning_effort=LlmReasoningEffort.MEDIUM,
            verbosity=LlmVerbosity.LOW,
            creativity=LlmCreativity.NONE,
            business=business,
            initiative=initiative,
            task_iteration=self.config.current_iteration,
            llm_model=self.default_llm_model,
            token_count=total_tokens,
            price=total_cost_usd,
            response=result.stdout,
            input_messages=[
                {
                    "role": LlmMessageType.SYSTEM,
                    "content": artifact_paths["prompt"].read_text(encoding="utf-8")
                },
                {
                    "role": LlmMessageType.USER,
                    "content": json.dumps(self.planning_data, indent=4)
                }
            ]
        )
        
        # Build metadata
        metadata_key = f"{self.coder_name}_metadata"
        planning_record = copy.deepcopy(self.config.current_iteration.planning_json or {})
        metadata = planning_record.get(metadata_key, {})
        
        if usage_metrics:
            metadata.update(usage_metrics)
        
        metadata.update({
            "stdout_path": str(artifact_paths["stdout"]),
            "stderr_path": str(artifact_paths["stderr"]),
            f"{self.coder_name}_start_time": start_time,
            "return_code": result.returncode,
            "execution_completed_at": time.time(),
            "attempt": attempt
        })
        
        # Add coder-specific metadata
        if "last_message" in artifact_paths and artifact_paths["last_message"].exists():
            metadata["last_message_path"] = str(artifact_paths["last_message"])
        if "session" in artifact_paths and artifact_paths["session"].exists():
            metadata["session_path"] = str(artifact_paths["session"])
        
        if feedback_sections:
            metadata["opentofu_feedback"] = feedback_sections.copy()
        
        planning_record[metadata_key] = metadata
        self._update_planning_json(planning_record)
        
        return metadata
    
    def _update_final_metadata(self, metadata: Dict) -> None:
        """Update final metadata with persisted code files."""
        planning_record = copy.deepcopy(self.config.current_iteration.planning_json or {})
        if not isinstance(planning_record, dict):
            planning_record = {}
        
        metadata_key = f"codefiles_metadata"
        coder_metadata = planning_record.get(metadata_key, {})
        coder_metadata.update(metadata)
        planning_record[metadata_key] = coder_metadata
        
        self._update_planning_json(planning_record)
    
    def _cleanup_artifacts(self, artifact_paths: Dict[str, Path]) -> None:
        """Clean up artifact files."""
        common.quietly_delete(list(artifact_paths.values()))
    
    def _persist_codex_code_versions(
            self,
            changed_paths: list
    ) -> list[str]:
        if not changed_paths:
            return []
        
        instruction_lookup = self._build_instruction_lookup()
        sandbox_root = self.config.sandbox_root_dir
        
        persisted = []
        skipped_non_text = []
        
        for rel_path in changed_paths:
            if self._should_skip_code_version(rel_path):
                continue
            
            normalized_path = self._normalize_relative_path(rel_path)
            absolute_path = sandbox_root / normalized_path
            
            if not absolute_path.exists() or absolute_path.is_dir():
                continue
            
            try:
                common.assert_in_sandbox(
                    sandbox_root,
                    absolute_path
                )
            except ValueError as ve:
                self.config.log(
                    f"Skipping file outside sandbox when persisting code version: {rel_path}",
                    ve
                )
                continue
            
            try:
                CodeFile.update_from_path(
                    self.config.current_iteration,
                    absolute_path,
                    code_instructions=instruction_lookup.get(normalized_path)
                )
                persisted.append(normalized_path)
            except UnicodeDecodeError:
                skipped_non_text.append(normalized_path)
                self.config.log(
                    f"Skipping non-text file while persisting code version: {normalized_path}"
                )
            except Exception as err:
                self.config.log(
                    f"Failed to persist code version for {normalized_path}",
                    err
                )
                raise
        
        if skipped_non_text:
            self.config.log("Codex change tracking skipped non-text files", skipped_non_text)
        
        return persisted
    
    def _collect_repo_changed_files(
            self,
            prior_file_checksum_map: dict[Path, int],
            readonly_entries: list
    ) -> list[Path]:
        current_file_mtime_map = self.get_file_checksum_map(self.config.sandbox_root_dir)
        
        read_only_files = [
            self.config.sandbox_root_dir / e['path']
            for e in readonly_entries
        ]
        
        files = [
            f
            for f, checksum in current_file_mtime_map.items()
            if checksum != prior_file_checksum_map.get(f)
        ]
        
        for f in files:
            if (self.config.sandbox_root_dir / f) in read_only_files:
                raise BadPlan(f"Codeplanner / writer modified the readonly file '{f}")
        
        return files
    
    def get_file_checksum_map(self, dir_name: Path) -> dict[Path, int]:
        return {
            f: common.get_checksum(dir_name / f)
            for f in common.iterate_files_deep(dir_name) if not self._should_skip_code_version(f)
        }
    
    def validate_all_changed_files(self, normalized_changed):
        from erieiron_autonomous_agent.coding_agents.code_writer.code_writer import validate_code
        
        """Validate all changed files using appropriate validators"""
        validation_errors = []
        
        try:
            self.config.stack_manager.validate_stack()
        except Exception as e:
            logging.exception(e)
            validation_errors.append(str(e))
        
        # Build a lookup from file paths to their validator information
        validator_lookup = {}
        if self.planning_data:
            for code_file_entry in self.planning_data.get("code_files", []):
                file_path = code_file_entry.get("code_file_path")
                validator = code_file_entry.get("validator")
                if file_path and validator:
                    validator_lookup[self._normalize_relative_path(file_path)] = validator
        
        for file_path in normalized_changed:
            full_path = self.config.sandbox_root_dir / file_path
            validator = validator_lookup.get(file_path)
            
            # Skip if file doesn't exist or is not a regular file
            if not full_path.exists() or not full_path.is_file():
                continue
            
            try:
                file_content = full_path.read_text(encoding="utf-8")
                validate_code(
                    self.config,
                    full_path,
                    file_content,
                    validator
                )
            except FileNotFoundError:
                validation_errors.append(f"`{file_path}` is missing after Codex execution; restore the file.")
            except OSError as read_exc:
                validation_errors.append(f"Unable to read `{file_path}` after Codex execution: {read_exc}")
            except CodeCompilationError as compile_exc:
                validation_errors.append(f"Validation failed for `{file_path}`: {compile_exc}")
            except Exception as exc:
                validation_errors.append(f"Unexpected validation error for `{file_path}`: {exc}")
        
        # Return the first validation error, or None if all files are valid
        return common.safe_join(validation_errors, "\n") if validation_errors else None
    
    def _normalize_relative_path(self, path: str | None) -> str:
        if not path:
            return ""
        
        normalized = str(path).strip().replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        return normalized
    
    def _should_skip_code_version(self, relative_path: str) -> bool:
        if not relative_path:
            return True
        
        relative_path = str(relative_path)
        lowered = relative_path.lower()
        if relative_path.split("/", 1)[0] == "artifacts":
            return True
        
        if lowered.endswith(".ds_store"):
            return True
        
        return False
    
    def _build_instruction_lookup(self) -> dict[str, list | dict]:
        lookup: dict[str, list | dict] = {}
        if not self.planning_data:
            return lookup
        
        for entry in common.ensure_list(self.planning_data.get("code_files")):
            path = self._normalize_relative_path(entry.get("code_file_path"))
            if not path:
                continue
            
            instructions = entry.get("instructions")
            dsl_instructions = entry.get("dsl_instructions")
            
            if instructions:
                lookup[path] = copy.deepcopy(instructions)
            elif dsl_instructions:
                lookup[path] = copy.deepcopy(dsl_instructions)
        
        return lookup
    
    def write_plan(self, artifact_paths):
        lesson_ids = common.uuids(self.planning_data.get("relevant_lessons"))
        
        asef = 1
        
        plan = {
            "implementation_directive": self.planning_data.get("implementation_directive"),
            "diagnostic_context": self.planning_data.get("diagnostic_context"),
            "relevant_lessons": [
                f"{a.pattern}. {a.trigger}. {a.lesson}"
                for a in AgentLesson.objects.filter(id__in=lesson_ids)
            ]
        }
        
        artifact_paths["plan"].write_text(json.dumps(plan, indent=2, default=str), encoding="utf-8")
