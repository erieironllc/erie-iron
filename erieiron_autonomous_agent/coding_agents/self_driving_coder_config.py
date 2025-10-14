import json
import logging
import pprint
import time
import traceback
from collections import defaultdict
from enum import auto
from pathlib import Path

from django.db import transaction

from erieiron_autonomous_agent.models import SelfDrivingTaskIteration, Task, SelfDrivingTask, Business, Initiative
from erieiron_common import common, ErieIronJSONEncoder
from erieiron_common.enums import LlmModel, TaskType, ErieEnum, AwsEnv
from erieiron_common.llm_apis.llm_interface import LlmMessage

ERIEIRON_PUBLIC_COMMON_VERSION = "v0.1.18"
TASK_DESC_CODE_WRITING = "code writing"
LAMBDA_PACKAGES_BUCKET = 'erieiron-lambda-packages'

COUNT_FULL_LOGS_IN_CONTEXT = 2
USE_CODEX = True


class SdaInitialAction(ErieEnum):
    CODE = auto()
    PLAN = auto()
    DEPLOY = auto()
    EVAL = auto()


MAP_TASKTYPE_TO_PLANNING_PROMPT = {
    TaskType.CODING_ML: "codeplanner--ml_trainer.md",
    TaskType.CODING_APPLICATION: "codeplanner--feature_development.md",
    TaskType.INITIATIVE_VERIFICATION: "codeplanner--initiative_verification.md",
    TaskType.DESIGN_WEB_APPLICATION: "codeplanner--web_designer.md",
    TaskType.TASK_EXECUTION: "codeplanner--executable_task.md",
}

ARTIFACTS = "artifacts"


class CodeReviewException(Exception):
    def __init__(self, review_data):
        self.bad_plan = review_data.get("plan_quality", []) != "VALID"
        self.review_data = review_data
        super().__init__("Code Review Failed")
    
    def get_issue_dicts(self) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
        return self.get_file_blockers_dict(), self.get_file_warnings_dict()
    
    def get_file_blockers_dict(self) -> dict[str, list[dict]]:
        d = defaultdict(list)
        
        for i in common.ensure_list(self.review_data.get("blocking_issues", [])):
            d[i['file']].append(i)
        
        return d
    
    def get_file_warnings_dict(self) -> dict[str, list[dict]]:
        d = defaultdict(list)
        
        for i in common.ensure_list(self.review_data.get("non_blocking_warnings", [])):
            d[i['file']].append(i)
        
        return d


class SelfDriverConfig:
    def __init__(self, self_driving_task: SelfDrivingTask):
        self.debug = True
        self.self_driving_task: SelfDrivingTask = self_driving_task
        self.task: Task = self_driving_task.task
        self.initiative: Initiative = self.task.initiative
        self.task_type: TaskType = TaskType(self.task.task_type)
        self.budget: float = self.task.max_budget_usd or 0
        self.business = Business.objects.get(initiative__tasks__id=self.task.id)
        self.guidance = LlmMessage.sys(self.task.guidance) if self.task.guidance else None
        self.sandbox_root_dir = Path(self.self_driving_task.sandbox_path)
        self.current_iteration: SelfDrivingTaskIteration = None
        self.previous_iteration: SelfDrivingTaskIteration = None
        self.iteration_to_modify: SelfDrivingTaskIteration = None
        self.log_path: Path = None
        self.log_f = None
        self.stop_tailing = None
        self.phase = SdaPhase.INIT
        
        if self.task_type.eq(TaskType.PRODUCTION_DEPLOYMENT):
            self.aws_env = AwsEnv.PRODUCTION
        else:
            self.aws_env = AwsEnv.DEV
        
        raw_domain_name, hosted_zone_id, certificate_arn = self.task.get_domain_and_cert(self.aws_env)
        self.domain_name = self.self_driving_task.namespace_domain_with_stack_identifier(
            raw_domain_name,
            self.aws_env
        )
        self.hosted_zone_id = hosted_zone_id
        self.certificate_arn = certificate_arn
        
        self.current_iteration: SelfDrivingTaskIteration = self_driving_task.get_most_recent_iteration()
        if self.current_iteration:
            self.previous_iteration, self.iteration_to_modify = self.current_iteration.get_relevant_iterations()
        else:
            self.previous_iteration = self.iteration_to_modify = None
        
        artifacts_root = self.sandbox_root_dir / ARTIFACTS
        artifacts_root.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir = artifacts_root
        self.git = self.self_driving_task.get_git()
        
        self.model_code_planning = LlmModel.OPENAI_GPT_5
        
        self.refresh_domain_metadata()
        # self.model_code_planning = LlmModel.OPENAI_GPT_4_1_MINI
    
    def refresh_domain_metadata(self) -> None:
        domain_name, hosted_zone_id, certificate_arn = self.task.get_domain_and_cert(self.aws_env)
        self.domain_name = (domain_name or "").rstrip('.').lower()
        self.hosted_zone_id = hosted_zone_id
        self.certificate_arn = certificate_arn
    
    def set_phase(self, phase: 'SdaPhase'):
        previous_phase = self.phase
        self.phase = phase
        log_content = self.log_path.read_text() if self.log_path else None
        self.log(f"\n\n\n======Phase Change ============")
        if self.current_iteration:
            self.log(f"Phase: {phase}; iteration_id: {self.current_iteration.id} (v{self.current_iteration.version_number})")
            if previous_phase in [SdaPhase.INIT]:
                self.current_iteration.log_content_init = log_content
            elif previous_phase in [SdaPhase.PLANNING, SdaPhase.CODING]:
                self.current_iteration.log_content_coding = log_content
            elif previous_phase in [SdaPhase.BUILD, SdaPhase.DEPLOY, SdaPhase.EXECUTION]:
                self.current_iteration.log_content_execution = log_content
            elif previous_phase in [SdaPhase.EVALUATE]:
                self.current_iteration.log_content_evaluation = log_content
            else:
                raise Exception(f"unhandled phase {previous_phase}")
        else:
            self.log(f"Phase: {phase}")
    
    def set_iteration(self, *args):
        args = common.flatten(args)
        
        self.current_iteration: SelfDrivingTaskIteration = common.first(args)
        if not self.current_iteration:
            raise "current_iteration cannot be None"
        
        self.previous_iteration: SelfDrivingTaskIteration = common.first(args[1:])
        if not self.previous_iteration:
            self.previous_iteration = self.current_iteration.get_previous_iteration()
        
        if not self.previous_iteration:
            self.previous_iteration = self.current_iteration
        
        self.iteration_to_modify: SelfDrivingTaskIteration = common.first(args[2:])
        if not self.iteration_to_modify:
            self.iteration_to_modify = self.current_iteration.start_iteration
        
        if not self.iteration_to_modify:
            self.iteration_to_modify = self.previous_iteration
        
        if not self.iteration_to_modify:
            self.iteration_to_modify = self.current_iteration
        
        self.reset_log()
    
    def iterate_if_necessary(self):
        if not self.current_iteration:
            self.set_iteration(self.self_driving_task.iterate())
    
    def init_log(self):
        self.log_path = self.artifacts_dir / f"{self.self_driving_task.id}.log"
        common.quietly_delete(self.log_path)
        
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.touch(exist_ok=True)
        
        self.log_f = open(self.log_path, "w")
        self.stop_tailing = self.start_log_tail_thread()
    
    def reset_log(self):
        self.close_log()
        self.init_log()
    
    def close_log(self):
        try:
            self.log_f.flush()
            self.log_f.close()
        except:
            ...
        if self.stop_tailing:
            self.stop_tailing.set()
    
    def cleanup_iteration(self):
        self.close_log()
        self.current_iteration = self.previous_iteration = self.iteration_to_modify = self.log_path = self.log_f = self.stop_tailing = None
    
    def start_log_tail_thread(self):
        # Start a background thread to tail the logfile contents to logging.info()
        import threading
        stop_tailing = threading.Event()
        iteration_version = self.current_iteration.version_number
        
        def tail_logfile():
            """Tail the logfile and stream new content to logging.info()"""
            try:
                last_position = 0
                while not stop_tailing.is_set():
                    try:
                        self.log_f.flush()
                    except:
                        ...
                    
                    try:
                        # Check if file exists and get its current size
                        if self.log_path.exists():
                            current_size = self.log_path.stat().st_size
                            if current_size > last_position:
                                # File has grown, read new content
                                with open(self.log_path, "r") as tail_f:
                                    tail_f.seek(last_position)
                                    new_content = tail_f.read(current_size - last_position)
                                    if new_content:
                                        new_content = common.truncate_text_lines(new_content)
                                        for line in new_content.splitlines():
                                            logging.info(f"(v{iteration_version}) {line}")
                                
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
    
    def log(self, *args):
        parts = []
        for arg in common.flatten(args):
            if isinstance(arg, dict):
                parts.append(json.dumps(arg, indent=4, cls=ErieIronJSONEncoder))
            elif isinstance(arg, Exception):
                parts.append(common.get_stack_trace_as_string(arg))
            else:
                parts.append(arg)
        
        s = common.safe_join(parts)
        if self.log_f:
            for line in common.safe_split(s, "\n", strip=False):
                self.log_f.write(f"{line}\n")
            self.log_f.flush()
        else:
            logging.info(s)
        
        if self.current_iteration:
            with transaction.atomic():
                truncated_log = common.truncate_text_lines(self.get_log_content())
                
                if self.phase in [SdaPhase.PLANNING, SdaPhase.CODING]:
                    SelfDrivingTaskIteration.objects.filter(id=self.current_iteration.id).update(
                        log_content_coding=truncated_log
                    )
                elif self.phase in [SdaPhase.DEPLOY, SdaPhase.EXECUTION]:
                    SelfDrivingTaskIteration.objects.filter(id=self.current_iteration.id).update(
                        log_content_execution=truncated_log
                    )
                elif SdaPhase.EVALUATE.eq(self.phase):
                    SelfDrivingTaskIteration.objects.filter(id=self.current_iteration.id).update(
                        log_content_evaluation=truncated_log
                    )
    
    def get_log_content(self):
        if not self.log_f:
            return ""
        
        try:
            self.log_f.flush()
            return self.log_path.read_text()
        except Exception:
            return traceback.format_exc()


class GoalAchieved(Exception):
    def __init__(self, planning_data):
        pprint.pprint(planning_data)
        self.planning_data = planning_data


class AgentBlocked(Exception):
    def __init__(self, blocked_data):
        self.blocked_data = blocked_data


class NeedPlan(Exception):
    def __init__(self, msg: str):
        super().__init__(msg)


class CloudFormationException(Exception):
    def __init__(self, extracted_exception: str):
        super().__init__(extracted_exception)


class CloudFormationStackDeleting(Exception):
    """Signal raised when a stack enters a delete workflow."""
    
    def __init__(self, stack_name: str, status: str, new_stack_name: str):
        self.stack_name = stack_name
        self.status = status
        self.new_stack_name = new_stack_name
        super().__init__(
            f"CloudFormation stack {stack_name} entered {status}; will pivot to {new_stack_name}"
        )


class ExecutionException(Exception):
    def __init__(self, extracted_exception: str):
        super().__init__(extracted_exception)


class BadPlan(Exception):
    def __init__(self, msg: str, plan_data: dict = None):
        if not plan_data:
            plan_data = {}
            
        self.plan_data = plan_data
        super().__init__(msg)


class RetryableException(Exception):
    ...


class SdaPhase(ErieEnum):
    INIT = auto()
    PLANNING = auto()
    CODING = auto()
    BUILD = auto()
    DEPLOY = auto()
    EXECUTION = auto()
    EVALUATE = auto()
