from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

from erieiron_autonomous_agent.coding_agents.coding_agent_config import (
    CodingAgentConfig,
    TASK_DESC_CODE_WRITING
)
from erieiron_autonomous_agent.coding_agents.self_driving_coder_exceptions import (
    BadPlan,
    CodeReviewException
)
from erieiron_autonomous_agent.models import SelfDrivingTaskIteration
from erieiron_common.enums import LlmModel


class IndividualFileCoder:
    """File-by-file coding implementation that works through planning data."""
    
    def __init__(self, config: CodingAgentConfig, planning_data: dict):
        super().__init__()
        self.config = config
        self.planning_data = planning_data
    
    @property
    def coder_name(self) -> str:
        return "individual"
    
    def execute_coding(self) -> Tuple[List[Path], Dict]:
        """Execute file-by-file coding and return (changed_paths, execution_metadata)."""
        self.config.current_iteration.codeversion_set.all().delete()
        
        cr_exception = None
        failed_code_reviews = []
        changed_paths = []
        metadata = {
            "coder_type": "individual_file",
            "review_iterations": 0,
            "files_processed": 0
        }
        
        for review_iteration_idx in range(5):
            metadata["review_iterations"] = review_iteration_idx + 1
            
            try:
                # Implement code changes file by file
                self._implement_code_changes(cr_exception)
                
                # Collect changed files
                changed_paths = self._collect_changed_files()
                metadata["files_processed"] = len(changed_paths)
                
                # Perform code review if no previous errors
                if not self.config.previous_iteration.has_error():
                    self._perform_code_review()
                
                # If we get here, both implementation and review succeeded
                break
            
            except CodeReviewException as code_review_exception:
                from erieiron_autonomous_agent.coding_agents.coding_agent import extract_lessons
                
                extract_lessons(
                    self.config,
                    TASK_DESC_CODE_WRITING,
                    code_review_exception.review_data
                )
                
                failed_code_reviews.append(code_review_exception.review_data)
                cr_exception = code_review_exception
                
                if code_review_exception.bad_plan:
                    raise BadPlan(
                        f"Code Review failed five times, time for a new plan. All code review blockers.",
                        {"failed_code_reviews": failed_code_reviews}
                    )
                elif review_iteration_idx == 4:
                    # Out of retries
                    raise BadPlan(
                        f"Code Review failed 5 times. Need a new plan.",
                        code_review_exception.review_data
                    )
        
        metadata["failed_code_reviews"] = failed_code_reviews
        return changed_paths, metadata
    
    def _implement_code_changes(
            self,
            code_review_exception: CodeReviewException
    ) -> SelfDrivingTaskIteration:
        """Implement code changes file by file based on planning data."""
        from erieiron_autonomous_agent.coding_agents.coding_agent import (
            extract_lessons,
            write_code_file
        )
        from erieiron_autonomous_agent.utils.codegen_utils import CodeCompilationError
        from erieiron_autonomous_agent.models import CodeFile
        import traceback
        import json
        
        current_iteration = self.config.current_iteration
        previous_iteration = self.config.previous_iteration
        iteration_to_modify = self.config.iteration_to_modify
        code_file_instructions = self.planning_data.get("code_files", [])
        
        if not code_file_instructions:
            raise BadPlan("no code files found", self.planning_data)
        
        if code_review_exception:
            code_review_file_blockers, code_review_file_warnings = code_review_exception.get_issue_dicts()
        else:
            code_review_file_blockers = code_review_file_warnings = defaultdict(list)
        
        # Process requirements.txt first
        code_file_instructions = (
                [cfi for cfi in code_file_instructions if cfi.get("code_file_path") == "requirements.txt"]
                +
                [cfi for cfi in code_file_instructions if cfi.get("code_file_path") != "requirements.txt"]
        )
        
        if previous_iteration and (previous_iteration != iteration_to_modify):
            roll_back_reason = self.planning_data.get("rollback_reason")
        else:
            roll_back_reason = None
        
        requirements_txt = CodeFile.get(self.config.business, "requirements.txt").get_latest_version().code
        
        for cfi in code_file_instructions:
            code_file_path_str: str = cfi.get("code_file_path")
            if code_file_path_str.startswith("/"):
                raise BadPlan(f"invalid file path: {code_file_path_str} - code file paths are forbidden from starting with a slash", self.planning_data)
            
            if code_file_path_str.startswith(str(self.config.sandbox_root_dir)):
                code_file_path_str = code_file_path_str[len(str(self.config.sandbox_root_dir)) + 1:]
            
            blocking_issues = code_review_file_blockers[code_file_path_str]
            if code_review_exception and not blocking_issues:
                # We are fixing a code review exception, but no changes to this file
                continue
            
            non_blocking_issues = code_review_file_warnings[code_file_path_str]
            
            code_file_path: Path = self.config.sandbox_root_dir / code_file_path_str
            if not code_file_path:
                raise BadPlan(f"missing code file name: {json.dumps(cfi)}", self.planning_data)
            
            if not code_file_path.exists():
                code_file_path.parent.mkdir(parents=True, exist_ok=True)
                code_file_path.touch()
            
            code_version_to_modify = iteration_to_modify.get_code_version(code_file_path)
            code_file = code_version_to_modify.code_file
            
            instructions = cfi.get("instructions", [])
            dsl_instructions = cfi.get("dsl_instructions", [])
            
            if not (instructions or dsl_instructions):
                self.config.log(f"no modifications for {code_file_path}")
                code_file.update(current_iteration, code_version_to_modify.code)
            else:
                previous_exception = None
                code_str = None
                
                for i in range(3):
                    try:
                        code_str = write_code_file(
                            config=self.config,
                            code_version_to_modify=code_version_to_modify,
                            code_file_data=cfi,
                            requirements_txt=requirements_txt,
                            blocking_issues=blocking_issues,
                            code_writing_model=LlmModel.valid_or(cfi.get("code_writing_model"), LlmModel.OPENAI_GPT_5_1),
                            roll_back_reason=roll_back_reason,
                            previous_exception=previous_exception
                        )
                        
                        previous_exception = None
                        break
                    
                    except CodeCompilationError as e:
                        extract_lessons(
                            self.config,
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
                    # Validation failed three times. Keep going, if it fails in deployment or execution 
                    # we'll have another chances at the feedback loop
                    self.config.log(previous_exception)
                
                if code_str:
                    code_file.update(
                        current_iteration,
                        code_str,
                        code_instructions=instructions
                    )
                    if code_file_path_str == "requirements.txt":
                        requirements_txt = code_str
        
        self.config.git.add_files()
        return current_iteration
    
    def _perform_code_review(self) -> None:
        """Perform code review on the implemented changes."""
        from erieiron_autonomous_agent.coding_agents.coding_agent import (
            get_architecture_docs,
            get_tombstone_message,
            get_file_structure_msg,
            get_previous_iteration_summaries_msg,
            get_lessons_msg,
            get_guidance_msg
        )
        from erieiron_autonomous_agent.system_agent_llm_interface import llm_chat, get_sys_prompt
        from erieiron_common.llm_apis.llm_interface import LlmMessage
        
        current_iteration = self.config.current_iteration
        iteration_to_modify = self.config.iteration_to_modify
        task = self.config.task
        
        messages = [
            get_sys_prompt([
                "codereviewer.md",
                "common--credentials_architecture_tofu.md"
            ]),
            get_architecture_docs(self.config.initiative),
            get_tombstone_message(self.config),
            get_file_structure_msg(self.config.sandbox_root_dir) if not iteration_to_modify.has_error() else [],
            get_previous_iteration_summaries_msg(self.config),
            get_lessons_msg("Relevant past lessons", self.config),
            get_guidance_msg(self.config),
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
            tag_entity=self.config.current_iteration,
            output_schema="codereviewer.md.schema.json"
        ).json()
        
        self.config.log(code_review_data)
        
        blocking_issues = code_review_data.get("blocking_issues", [])
        non_blocking_warnings = code_review_data.get("non_blocking_warnings", [])
        
        if blocking_issues:
            raise CodeReviewException(code_review_data)
        elif non_blocking_warnings:
            self.config.log(non_blocking_warnings)
    
    def _collect_changed_files(self) -> List[Path]:
        """Collect the list of files that were changed during this iteration."""
        code_file_instructions = self.planning_data.get("code_files", [])
        changed_paths = []
        
        for cfi in code_file_instructions:
            code_file_path_str = cfi.get("code_file_path")
            if code_file_path_str.startswith(str(self.config.sandbox_root_dir)):
                code_file_path_str = code_file_path_str[len(str(self.config.sandbox_root_dir)) + 1:]
            
            code_file_path = self.config.sandbox_root_dir / code_file_path_str
            if code_file_path.exists():
                changed_paths.append(code_file_path)
        
        return changed_paths
