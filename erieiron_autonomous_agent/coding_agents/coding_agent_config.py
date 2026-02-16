import json
import logging
import os
import time
import traceback
import weakref
from enum import auto
from pathlib import Path

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from erieiron_autonomous_agent import system_agent_llm_interface
from erieiron_autonomous_agent.coding_agents.frontier_session import FrontierSession
from erieiron_autonomous_agent.models import SelfDrivingTaskIteration, Task, SelfDrivingTask, Business, Initiative, InfrastructureStack
from erieiron_autonomous_agent.system_agent_llm_interface import llm_chat, get_sys_prompt
from erieiron_common import common, ErieIronJSONEncoder, aws_utils
from erieiron_common.aws_utils import sanitize_aws_name
from erieiron_common.enums import BuildStep, LlmReasoningEffort
from erieiron_common.enums import LlmModel, TaskType, ErieEnum, InfrastructureStackType, SdaPhase, CredentialsSpace, IterationMode
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_common.stack_manager import StackManager

ERIEIRON_PUBLIC_COMMON_VERSION = "v0.1.32"
COUNT_TEST_RUNS_REQUIRED = 3
TASK_DESC_CODE_WRITING = "code writing"
LAMBDA_PACKAGES_BUCKET = 'erieiron-lambda-packages'

MIN_PODMAN_STORAGE_FREE_GB = 4.0
COUNT_FULL_LOGS_IN_CONTEXT = 2

os.environ["DOCKER_DEFAULT_PLATFORM"] = "linux/arm64"


class SdaInitialAction(ErieEnum):
    CODE = auto()
    PLAN = auto()
    DEPLOY = auto()
    STACK_PUSH = auto()
    EVAL = auto()


MAP_TASKTYPE_TO_PLANNING_PROMPT = {
    TaskType.CODING_ML: "codeplanner--ml_trainer.md",
    TaskType.CODING_APPLICATION: "codeplanner--feature_development.md",
    TaskType.PRODUCTION_DEPLOYMENT: "codeplanner--production_deployment.md",
    TaskType.INITIATIVE_VERIFICATION: "codeplanner--initiative_verification.md",
    TaskType.DESIGN_WEB_APPLICATION: "codeplanner--web_designer.md",
    TaskType.TASK_EXECUTION: "codeplanner--executable_task.md",
}

ARTIFACTS = "artifacts"

ENVVAR_TO_STACK_OUTPUT = {
    'AWS_REGION': 'AwsRegion',
    'AWS_DEFAULT_REGION': 'AwsRegion',
    'ERIEIRON_DB_NAME': 'RdsInstanceDBName',
    'ERIEIRON_DB_HOST': 'RdsInstanceEndpoint',
    'ERIEIRON_DB_PORT': 'RdsInstancePort'
}


class CodingAgentConfig:
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self.close_log()
        except:
            ...
    
    def __init__(self, self_driving_task: SelfDrivingTask, one_off_action=None):
        self._finalizer = weakref.finalize(self, self.close_log)
        self.debug = True
        self.start_time = common.get_now()
        self.one_off_action = one_off_action
        self.self_driving_task: SelfDrivingTask = self_driving_task
        self.task: Task = self_driving_task.task
        self.initiative: Initiative = self.task.initiative
        self.task_type: TaskType = TaskType(self.task.task_type)
        self.budget: float = self.task.max_budget_usd or 0
        self.business: Business = Business.objects.get(initiative__tasks__id=self.task.id)
        self.guidance = LlmMessage.sys(self.task.guidance) if self.task.guidance else None
        self.sandbox_root_dir = Path(self.self_driving_task.sandbox_path)
        self.current_iteration: SelfDrivingTaskIteration = None
        self.previous_iteration: SelfDrivingTaskIteration = None
        self.iteration_to_modify: SelfDrivingTaskIteration = None
        self.log_path: Path = None
        self.log_f = None
        self.stop_tailing = None
        self._build_plan = None
        self.phase = SdaPhase.INIT
        
        # UI-first phase tracking
        self.is_ui_first_phase = self.task.is_ui_first_phase()
        logging.info(f"Task {self.task.id} UI-first phase: {self.is_ui_first_phase}")
        
        # self.self_driving_task.test_file_path = Path(self.self_driving_task.test_file_path).relative_to(self.sandbox_root_dir)
        # self.self_driving_task.save()
        
        self.current_iteration: SelfDrivingTaskIteration = self_driving_task.get_most_recent_iteration()
        if self.current_iteration:
            self.previous_iteration, self.iteration_to_modify = self.current_iteration.get_relevant_iterations()
        else:
            self.previous_iteration = self.iteration_to_modify = None
        
        self.is_stagnating = common.get(self.previous_iteration, ["evaluation_json", "is_stagnating"], False)
        
        artifacts_root = self.sandbox_root_dir / ARTIFACTS
        artifacts_root.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir = artifacts_root
        self.git = self.self_driving_task.get_git()
        self.deployment_logs = []
        self.ecr_repo_name = sanitize_aws_name(self.business.service_token)
        
        # Get stack using resolved env_type
        self.env_type = self.task.resolve_target_env_type(InfrastructureStackType.APPLICATION)
        self.stack = InfrastructureStack.get_stack(
            self.initiative,
            InfrastructureStackType.APPLICATION,
            self.env_type
        )
        
        self.runtime_env = self.stack.get_runtime_env()
        self.cloud_account = self.stack.get_cloud_account()
        
        self.aws_interface = aws_utils.get_aws_interface(self.cloud_account)
        self.vpc_configuration = self.aws_interface.get_shared_vpc()
        self.aws_interface.configure_nat_gateway()
        
        self.domain_manager = self.business.get_domain_manager(self.cloud_account)

        self.stack_manager = StackManager(
            self.stack,
            self.runtime_env,
            self.sandbox_root_dir
        )

        try:
            self.stack_manager.init_workspace()
        except Exception as e:
            logging.exception(e)

        self.frontier_session = FrontierSession(self)
    
    def get_build_plan(self, reset=False):
        if reset or not self._build_plan:
            self._generate_build_plan()
        
        return self._build_plan
    
    def _generate_build_plan(self):
        if self.current_iteration.version_number < 2:
            previous_container_tag = self.task.get_container_image_tag()
            tag_exists_in_ecr = previous_container_tag and self.aws_interface.tag_exists_in_ecr(
                self.ecr_repo_name,
                previous_container_tag,
                self.env_type.get_aws_region()
            )
            
            steps = []
            if not tag_exists_in_ecr:
                steps = steps + [
                    BuildStep.BUILD_CONTAINER,
                    BuildStep.PUSH_TO_ECR
                ]
                
            steps.append(BuildStep.DEPLOY_TO_AWS)
            return self.set_build_plan(steps)
        
        modified_files = list(self.current_iteration.codeversion_set.all())
        if modified_files:
            modified_files_msg = LlmMessage.user_from_data(
                "Modified Files", [
                    cv.code_file.file_path
                    for cv in modified_files
                ], "modified_file")
        else:
            modified_files_msg = "No files modified during this iteration"
        
        # Get build plan from LLM agent
        build_plan = llm_chat(
            "Plan Build Steps",
            [
                get_sys_prompt("build_planner.md"),
                modified_files_msg
            ],
            output_schema="build_planner.md.schema.json",
            model=LlmModel.OPENAI_GPT_5_NANO,
            tag_entity=self.current_iteration,
            reasoning_effort=LlmReasoningEffort.LOW
        ).json()
        
        return self.set_build_plan(build_plan)
    
    def set_build_plan(self, build_plan: dict):
        if isinstance(build_plan, dict):
            self._build_plan = build_plan
        else:
            self._build_plan = {
                BuildStep(k): True
                for k in common.ensure_list(build_plan)
            }
        
        self.log_build_plan()
        return self._build_plan
    
    def get_default_build_plan(self, ops=None, reasoning="Default build plan"):
        base_plan = {
            "REASONING": reasoning,
            BuildStep.BUILD_CONTAINER: True,
            BuildStep.PUSH_TO_ECR: True,
            BuildStep.RUN_BACKEND_TESTS: True,
            BuildStep.RUN_FRONTEND_TESTS: True,
            BuildStep.BUILD_LAMBDAS: True,
            BuildStep.RUN_TESTS_LOCALLY: False,
            BuildStep.RUN_TESTS_IN_CONTAINER: False,
            BuildStep.DEPLOY_TO_AWS: True,
            BuildStep.MIGRATE_DATABASE: False,
            BuildStep.UPDATE_STACK_TF: False
        }
        
        # Production deployment: always build and deploy regardless of file changes
        if TaskType.PRODUCTION_DEPLOYMENT.eq(self.task_type):
            base_plan[BuildStep.BUILD_CONTAINER] = True
            base_plan[BuildStep.DEPLOY_TO_AWS] = True
            base_plan[BuildStep.RUN_TESTS_LOCALLY] = False
            return base_plan
        
        # UI-first phase: build locally but don't deploy to AWS
        if self.is_ui_first_phase:
            base_plan[BuildStep.DEPLOY_TO_AWS] = False
            base_plan[BuildStep.PUSH_TO_ECR] = False
            return base_plan
        
        # Apply iteration mode adjustments
        mode = self.self_driving_task.iteration_mode if self.self_driving_task else None
        
        if IterationMode.LOCAL_TESTS.eq(mode):
            # LOCAL_TESTS mode: run tests locally, no container build
            base_plan[BuildStep.BUILD_CONTAINER] = False
            base_plan[BuildStep.RUN_TESTS_LOCALLY] = True
            base_plan[BuildStep.DEPLOY_TO_AWS] = False
        
        elif IterationMode.CONTAINER_TESTS.eq(mode):
            # CONTAINER_TESTS mode: build container and run tests, but don't deploy yet
            base_plan[BuildStep.BUILD_CONTAINER] = True
            base_plan[BuildStep.DEPLOY_TO_AWS] = False
        
        elif IterationMode.AWS_DEPLOYMENT.eq(mode):
            # AWS_DEPLOYMENT mode: build container and deploy
            base_plan[BuildStep.BUILD_CONTAINER] = True
            base_plan[BuildStep.DEPLOY_TO_AWS] = True
        
        if ops:
            base_plan.update(ops)
        
        return base_plan
    
    def log_build_plan(self):
        plan = self._build_plan
        
        self.log(f"\n{'=' * 80}")
        self.log(f"BUILD PLAN ({plan.get('REASONING', '')}):")
        for k in sorted(plan):
            if plan[k] and k != "REASONING":
                self.log(f"\t* {k}")
        self.log(f"{'=' * 80}\n")
    
    def get_env_for_credentials_space(self, credential_space: CredentialsSpace):
        if CredentialsSpace.ERIE_IRON.eq(credential_space):
            stack = Business.get_erie_iron_business().infrastructurestack_set.first()
            return stack.get_runtime_env()
        else:
            return self.stack.get_runtime_env()
    
    def add_deployment_log(self, log_results: dict):
        self.deployment_logs.append(log_results)
    
    def get_stack_names(self) -> list[str]:
        return [
            s.stack_name
            for s in InfrastructureStack.objects
            .filter(initiative=self.initiative, env_type=self.env_type)
            .order_by("created_timestamp")
        ]
    
    def set_phase(self, phase: 'SdaPhase'):
        previous_phase = self.phase
        self.phase = phase
        log_content = self.log_path.read_text() if self.log_path else None
        self.reset_log()
        
        self.log(f"\n\n\n======Phase Change ============")
        if self.current_iteration:
            self.log(f"Phase: {phase}; iteration_id: {self.current_iteration.id} (v{self.current_iteration.version_number})")
            if previous_phase in [SdaPhase.INIT]:
                log_field = "log_content_init"
            elif previous_phase in [SdaPhase.PLANNING, SdaPhase.CODING]:
                log_field = "log_content_coding"
            elif previous_phase in [SdaPhase.BUILD, SdaPhase.DEPLOY, SdaPhase.EXECUTION]:
                log_field = "log_content_execution"
            elif previous_phase in [SdaPhase.EVALUATE]:
                log_field = "log_content_evaluation"
            else:
                raise Exception(f"unhandled phase {previous_phase}")
            
            self.current_iteration.refresh_from_db(fields=[log_field])
            current_log = getattr(self.current_iteration, log_field)
            with transaction.atomic():
                SelfDrivingTaskIteration.objects.filter(id=self.current_iteration.id).update(
                    **{log_field: "\n".join(common.filter_none([current_log, log_content]))}
                )
            self.current_iteration.refresh_from_db(fields=[log_field])
        else:
            self.log(f"Phase: {phase}")
        
        self._record_phase_change()
        return log_content
    
    def _record_phase_change(self):
        if not self.self_driving_task:
            return
        now = timezone.now()
        updated = SelfDrivingTask.objects.filter(id=self.self_driving_task.id).update(
            phase_change_seq=F('phase_change_seq') + 1,
            latest_phase_change_at=now
        )
        if updated:
            current_seq = (self.self_driving_task.phase_change_seq or 0) + 1
            self.self_driving_task.phase_change_seq = current_seq
            self.self_driving_task.latest_phase_change_at = now
    
    def set_iteration(self, current_iteration: SelfDrivingTaskIteration):
        if current_iteration and current_iteration.self_driving_task_id != self.self_driving_task.id:
            raise Exception(f"cannot set current iteration to an iteration from a different task")
        
        self.current_iteration = current_iteration
        if not self.current_iteration:
            self.iterate_if_necessary()
        
        self.previous_iteration, self.iteration_to_modify = self.current_iteration.get_relevant_iterations()
        
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
    
    def get_code_planning_model(self) -> LlmModel:
        return system_agent_llm_interface.get_reasoning_model()
    
    def cleanup_iteration(self):
        self.close_log()
        self.current_iteration = self.previous_iteration = self.iteration_to_modify = self.log_path = self.log_f = self.stop_tailing = None
        common.quietly_delete(self.sandbox_root_dir / "artifacts")
        
    
    def start_log_tail_thread(self):
        # Start a background thread to tail the logfile contents to logging.info()
        import threading
        stop_tailing = threading.Event()
        iteration_version = self.current_iteration.version_number if self.current_iteration else "0"
        
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
    
    def dump_env_to_envrc(self):
        # Dump runtime_env to .envrc file
        envrc_path = self.sandbox_root_dir / ".envrc"
        if envrc_path.exists():
            # Read existing content and find the Erie Iron section
            existing_content = envrc_path.read_text()
            marker = "### START ERIE IRON ENV ###"
            if marker in existing_content:
                # Remove everything from the marker onwards
                envrc_content = existing_content[:existing_content.index(marker)]
            else:
                envrc_content = existing_content
        else:
            envrc_content = ""
        
        # Append Erie Iron environment variables
        envrc_content += "### START ERIE IRON ENV ###\n"
        for key, value in self.runtime_env.items():
            # Escape double quotes in the value
            escaped_value = str(value).replace('"', '\\"')
            envrc_content += f'export {key}="{escaped_value}"\n'
        
        envrc_path.write_text(envrc_content)
    
    def dump_env_to_env_file(self):
        # Dump runtime_env to .envrc file
        envrc_path = self.sandbox_root_dir / ".env"
        if envrc_path.exists():
            # Read existing content and find the Erie Iron section
            existing_content = envrc_path.read_text()
            marker = "### START ERIE IRON ENV ###"
            if marker in existing_content:
                # Remove everything from the marker onwards
                envrc_content = existing_content[:existing_content.index(marker)]
            else:
                envrc_content = existing_content
        else:
            envrc_content = ""
        
        # Append Erie Iron environment variables
        envrc_content += "### START ERIE IRON ENV ###\n"
        for key, value in self.runtime_env.items():
            # Escape double quotes in the value
            escaped_value = str(value).replace('"', '\\"')
            envrc_content += f'{key}="{escaped_value}"\n'
        
        envrc_path.write_text(envrc_content)
