import copy
import json
import textwrap
import time
from abc import ABC, abstractmethod
from typing import Dict, Tuple, List, Optional
from pathlib import Path

from django.db import transaction

from erieiron_common import common
from erieiron_common.enums import LlmReasoningEffort, LlmVerbosity, LlmMessageType
from erieiron_autonomous_agent.coding_agents.coding_agent_config import (
    CodingAgentConfig, 
    TASK_DESC_CODE_WRITING
)
from erieiron_autonomous_agent.models import (
    LlmRequest,
    SelfDrivingTaskIteration,
)


class BaseCoder(ABC):
    """Abstract base class for coding implementations with common functionality."""
    
    @property
    @abstractmethod
    def coder_name(self) -> str:
        """Return the name of this coder (e.g., 'codex', 'claude', 'gemini')."""
        pass
    
    @property
    @abstractmethod
    def default_llm_model(self):
        """Return the default LLM model for this coder."""
        pass
    
    @abstractmethod
    def build_command(self, config: CodingAgentConfig, prompt_path: Path, artifact_paths: Dict[str, Path]) -> List[str]:
        """Build the CLI command for this coder."""
        pass
    
    @abstractmethod
    def execute_command(self, command: List[str], config: CodingAgentConfig, prompt_text: str) -> 'subprocess.CompletedProcess':
        """Execute the CLI command and return the result."""
        pass
    
    @abstractmethod
    def check_for_api_errors(self, result: 'subprocess.CompletedProcess') -> None:
        """Check for API-specific errors and raise appropriate exceptions."""
        pass
    
    @abstractmethod
    def extract_usage_stats(self, stdout: str, stderr: str, metadata: dict) -> Dict:
        """Extract token/cost metrics from execution output."""
        pass
    
    def execute_coding(self, config: CodingAgentConfig, planning_data: dict) -> Tuple[List[Path], Dict]:
        """Execute coding and return (changed_paths, execution_metadata)."""
        from erieiron_autonomous_agent.coding_agents.coding_agent import (
            get_file_checksum_map,
            _collect_repo_changed_files, 
            _persist_codex_code_versions,
            validate_all_changed_files,
            _normalize_relative_path,
            get_lessons,
            extract_lessons
        )
        
        config.current_iteration.codeversion_set.all().delete()
        config.log(f"Starting {self.coder_name.title()} CLI planning/execution pipeline")
        
        # Set up artifact paths
        artifact_paths = self._setup_artifact_paths(config)
        
        try:
            # Build common data structures
            readonly_entries, readonly_lines = self._build_readonly_entries(config)
            code_file_paths, code_file_summary_lines = self._build_code_file_entries(planning_data)
            business_context = self._extract_business_context(config)
            
            # Save plan
            self._save_plan(artifact_paths["plan"], planning_data)
            
            # Build prompt
            prompt_text = self._build_prompt(config, planning_data, business_context, readonly_lines, code_file_summary_lines, artifact_paths["plan"])
            artifact_paths["prompt"].write_text(prompt_text, encoding="utf-8")
            
            # Update planning metadata
            augmented_plan = self._create_augmented_plan(planning_data, artifact_paths, code_file_paths)
            self._update_planning_json(config, augmented_plan)
            
            # Build command
            command = self.build_command(config, artifact_paths["prompt"], artifact_paths)
            
            # Execute with validation loop
            changed_paths, metadata = self._execute_with_validation_loop(
                config, planning_data, command, prompt_text, artifact_paths, 
                readonly_entries, business_context
            )
            
            # Persist code versions
            persisted_code_files = _persist_codex_code_versions(config, changed_paths, planning_data)
            
            # Update final metadata
            metadata["persisted_code_files"] = persisted_code_files
            self._update_final_metadata(config, metadata)
            
            config.log(f"Stored code versions for {self.coder_name.title()}-modified files", persisted_code_files)
            config.git.add_files()
            
            return changed_paths, metadata
            
        finally:
            self._cleanup_artifacts(artifact_paths)
    
    def _setup_artifact_paths(self, config: CodingAgentConfig) -> Dict[str, Path]:
        """Set up artifact file paths."""
        iteration_id = config.current_iteration.id
        artifacts_dir = config.artifacts_dir
        
        paths = {
            "plan": artifacts_dir / f"{iteration_id}_plan.json",
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
    
    def _build_readonly_entries(self, config: CodingAgentConfig) -> Tuple[List[Dict], List[str]]:
        """Build read-only entries and formatted lines."""
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
        
        return readonly_entries, readonly_lines
    
    def _build_code_file_entries(self, planning_data: dict) -> Tuple[List[str], List[str]]:
        """Build code file entries and summary lines."""
        code_file_entries = common.ensure_list(planning_data.get("code_files"))
        code_file_paths = [
            entry.get("code_file_path")
            for entry in code_file_entries
            if isinstance(entry, dict) and entry.get("code_file_path")
        ]
        code_file_summary_lines = [f"- {path}" for path in code_file_paths]
        return code_file_paths, code_file_summary_lines
    
    def _extract_business_context(self, config: CodingAgentConfig) -> Dict:
        """Extract business, initiative, and task context."""
        return {
            "business": config.business,
            "initiative": config.initiative,
            "task": config.task
        }
    
    def _save_plan(self, plan_path: Path, planning_data: dict) -> None:
        """Save planning data to JSON file."""
        plan_path.write_text(json.dumps(planning_data, indent=2, default=str), encoding="utf-8")
    
    def _build_prompt(self, config: CodingAgentConfig, planning_data: dict, business_context: Dict, 
                     readonly_lines: List[str], code_file_summary_lines: List[str], plan_path: Path) -> str:
        """Build the complete prompt for the coder."""
        from erieiron_autonomous_agent.coding_agents.coding_agent import get_lessons
        
        business = business_context["business"]
        initiative = business_context["initiative"]
        task = business_context["task"]
        
        # Reference prompts (common across all coders)
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
        
        # Build prompt parts
        prompt_parts = [
            self._get_coder_intro(config, plan_path),
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
        
        # Add optional sections
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
            
        if business.ui_design_spec:
            prompt_parts.append(textwrap.dedent(f"""
            ### UI Design Spec - UI code must conform to this specification
            {business.ui_design_spec}
            """))
        
        # Lessons learned
        prompt_parts.append(textwrap.dedent(f"""
        ### Lessons Learned - avoid repeating these errors
        {json.dumps(get_lessons(config, TASK_DESC_CODE_WRITING), indent=4)}
        """))
        
        # Additional guidance
        if config.guidance:
            prompt_parts.append(textwrap.dedent(f"""
            ## Important Additional Guidance
            {config.guidance}
            """))
        
        # Read-only paths
        if readonly_lines:
            prompt_parts.append(textwrap.dedent("""
            ## Read-only Paths - NEVER modify these
            """ + "\n".join(readonly_lines)))
        
        # Highlighted files
        if code_file_summary_lines:
            prompt_parts.append(textwrap.dedent("""
            ## Files Highlighted by the Plan
            Review instructions for each file in the plan JSON and modify only what is necessary:
            """ + "\n".join(code_file_summary_lines)))
        
        # Add reference prompts
        for path in reference_prompts:
            try:
                content = Path(path).read_text()
                prompt_parts.append(textwrap.dedent(f"""
                
                ### Reference: {path}
                {content}
                """))
            except FileNotFoundError:
                config.log(f"Warning: Reference prompt not found: {path}")
        
        # Route53 guardrail
        guardrail_marker = "Route53 Root Alias Guardrail"
        if not any(guardrail_marker in part for part in prompt_parts):
            prompt_parts.append(textwrap.dedent("""

            ### Route53 Root Alias Guardrail
            - Domain DNS must be published with Route53 `AWS::Route53::RecordSet` alias records. Create `Type: A` (and `AAAA` when IPv6 is required) entries that target the Application Load Balancer via `AliasTarget.DNSName` and `AliasTarget.HostedZoneId`.
            - Do **not** create a `CNAME` for `!Ref DomainName`, even when it contains subdomains; apex-style aliases keep Route53 compliant with DNS standards.
            - Continue using CNAMEs only for tokenized SES sub-records such as DKIM keys.
            """))
        
        # Add development plan and execution checklist
        prompt_parts.extend(self._get_final_prompt_sections(planning_data, plan_path))
        
        return "\n\n".join(part.strip() for part in prompt_parts if part)
    
    @abstractmethod
    def _get_coder_intro(self, config: CodingAgentConfig, plan_path: Path) -> str:
        """Get coder-specific introduction text."""
        pass
    
    @abstractmethod
    def _get_final_prompt_sections(self, planning_data: dict, plan_path: Path) -> List[str]:
        """Get coder-specific final prompt sections (plan, checklist, etc.)."""
        pass
    
    def _create_augmented_plan(self, planning_data: dict, artifact_paths: Dict[str, Path], code_file_paths: List[str]) -> dict:
        """Create augmented plan with metadata."""
        augmented_plan = copy.deepcopy(planning_data)
        metadata_key = f"{self.coder_name}_metadata"
        augmented_plan[metadata_key] = {
            "plan_path": str(artifact_paths["plan"]),
            "prompt_path": str(artifact_paths["prompt"]),
            "code_file_paths": code_file_paths,
        }
        return augmented_plan
    
    def _update_planning_json(self, config: CodingAgentConfig, planning_data: dict) -> None:
        """Update the planning JSON in the database."""
        with transaction.atomic():
            SelfDrivingTaskIteration.objects.filter(id=config.current_iteration.id).update(
                planning_json=planning_data
            )
        config.current_iteration.refresh_from_db(fields=["planning_json"])
    
    def _execute_with_validation_loop(self, config: CodingAgentConfig, planning_data: dict, 
                                     command: List[str], prompt_text: str, artifact_paths: Dict[str, Path],
                                     readonly_entries: List[Dict], business_context: Dict) -> Tuple[List[Path], Dict]:
        """Execute command with validation feedback loop."""
        from erieiron_autonomous_agent.coding_agents.coding_agent import (
            get_file_checksum_map,
            _collect_repo_changed_files,
            validate_all_changed_files,
            _normalize_relative_path,
            extract_lessons
        )
        
        prior_file_checksum_map = get_file_checksum_map(config.sandbox_root_dir)
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
            
            config.log(
                f"Running {self.coder_name.title()} CLI (attempt {attempt})",
                " ".join(command) if isinstance(command, list) else str(command),
                f"Prompt saved to {artifact_paths['prompt']}"
            )
            
            start_time = time.time()
            result = self.execute_command(command, config, prompt_with_feedback)
            
            # Save output
            artifact_paths["stdout"].write_text(result.stdout or "", encoding="utf-8")
            artifact_paths["stderr"].write_text(result.stderr or "", encoding="utf-8")
            
            # Check for API errors
            self.check_for_api_errors(result)
            
            # Extract usage stats
            usage_metrics = self.extract_usage_stats(
                result.stdout,
                result.stderr,
                {"config": config, **{k: v for k, v in artifact_paths.items() if k in ["last_message", "session"]}}
            )
            
            # Update metadata and create LLM request
            metadata = self._create_execution_metadata(
                config, planning_data, artifact_paths, start_time, result, attempt, 
                feedback_sections, usage_metrics, business_context
            )
            
            config.log(
                f"{self.coder_name.title()} CLI completed successfully",
                {
                    "stdout_path": str(artifact_paths["stdout"]),
                    "stderr_path": str(artifact_paths["stderr"]),
                    "attempt": attempt
                }
            )
            
            # Collect changed files
            changed_paths = _collect_repo_changed_files(config, prior_file_checksum_map, readonly_entries)
            
            if not changed_paths:
                from erieiron_autonomous_agent.coding_agents.self_driving_coder_exceptions import BadPlan
                raise BadPlan(f"{self.coder_name.title()} CLI produced no persistable file changes")
            
            # Validate changes
            normalized_changed = {_normalize_relative_path(p) for p in changed_paths}
            validation_error = validate_all_changed_files(config, normalized_changed, planning_data)
            
            if validation_error is None:
                break
            
            if attempt >= max_validation_attempts:
                raise validation_error
            
            # Extract lessons and add feedback
            extract_lessons(config, TASK_DESC_CODE_WRITING, validation_error)
            feedback_sections.append(
                textwrap.dedent(
                    f"""
                    Code validation failed with the following error:
                    {validation_error}

                    Apply the error details above to correct the problem
                    """
                ).strip()
            )
            config.log(
                f"OpenTofu validation failed after {self.coder_name.title()} execution; retrying with feedback",
                str(validation_error)
            )
        else:
            from erieiron_autonomous_agent.coding_agents.self_driving_coder_exceptions import ExecutionException
            raise ExecutionException(
                f"{self.coder_name.title()} CLI reached maximum validation attempts without resolving OpenTofu validation errors."
            )
        
        return changed_paths, metadata
    
    def _create_execution_metadata(self, config: CodingAgentConfig, planning_data: dict, 
                                  artifact_paths: Dict[str, Path], start_time: float,
                                  result: 'subprocess.CompletedProcess', attempt: int,
                                  feedback_sections: List[str], usage_metrics: Dict,
                                  business_context: Dict) -> Dict:
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
            business=business,
            initiative=initiative,
            task_iteration=config.current_iteration,
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
                    "content": json.dumps(planning_data, indent=4)
                }
            ]
        )
        
        # Build metadata
        metadata_key = f"{self.coder_name}_metadata"
        planning_record = copy.deepcopy(config.current_iteration.planning_json or {})
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
        self._update_planning_json(config, planning_record)
        
        return metadata
    
    def _update_final_metadata(self, config: CodingAgentConfig, metadata: Dict) -> None:
        """Update final metadata with persisted code files."""
        metadata_key = f"{self.coder_name}_metadata"
        planning_record = copy.deepcopy(config.current_iteration.planning_json or {})
        if not isinstance(planning_record, dict):
            planning_record = {}
        
        coder_metadata = planning_record.get(metadata_key, {})
        coder_metadata.update(metadata)
        planning_record[metadata_key] = coder_metadata
        
        self._update_planning_json(config, planning_record)
    
    def _cleanup_artifacts(self, artifact_paths: Dict[str, Path]) -> None:
        """Clean up artifact files."""
        common.quietly_delete(list(artifact_paths.values()))