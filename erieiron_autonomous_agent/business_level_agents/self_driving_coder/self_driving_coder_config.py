import random
from pathlib import Path

from erieiron_common import common
from erieiron_common.enums import LlmModel, TaskExecutionMode
from erieiron_common.llm_apis.llm_interface import CODE_MODELS_IN_ORDER
from erieiron_common.models import CodeFile, SelfDrivingTask, SelfDrivingTaskIteration, Business, Task

ARTIFACTS = "artifacts"


class GoalAchieved(Exception):
    def __init__(self, planning_data):
        self.planning_data = planning_data


class AgentBlocked(Exception):
    def __init__(self, blocked_data):
        self.blocked_data = blocked_data


class SelfDriverConfigException(Exception):
    pass


class SelfDriverConfig:
    @staticmethod
    def get(config_file: Path = None, task_id: str = None) -> 'SelfDriverConfig':
        if config_file:
            return SelfDriverConfig.from_config(config_file)
        elif task_id:
            return SelfDriverConfig.from_task(task_id)
        else:
            raise Exception("either config_file or task_id must be supplied")

    @staticmethod
    def from_config(config_file: Path) -> 'SelfDriverConfig':
        config_file = common.assert_exists(config_file)
        if config_file.name.endswith(".json"):
            try:
                config = common.read_json(config_file)
            except Exception as e:
                raise SelfDriverConfigException(e)
        else:
            config = {
                "goal": config_file.read_text()
            }

        config_base_name = common.safe_filename(config_file)
        main_code_path = Path(config.get("main_file", config_file.parent / f"{config_base_name}.py"))

        business = Business.objects.filter(id=config.get("business_id")).first()
        if not business:
            business = Business.get_erie_iron_business()

        self_driving_task, _ = SelfDrivingTask.objects.get_or_create(
            config_file=str(config_file),
            defaults={
                "main_name": config_base_name,
                "goal": config.get("goal"),
                "business": business
            }
        )

        return SelfDriverConfig({
            **config,
            "sandbox_root_dir": main_code_path.parent,
            "supress_eval": False,
            "code_directory": main_code_path.parent,
            "artifacts_dir": main_code_path.parent / ARTIFACTS / config_base_name,
            "log_path": Path(config.get("log_file", config_file.parent / f"{common.get_basename(config_file)}.output.log")),
            "self_driving_task": self_driving_task,
        })

    @staticmethod
    def from_task(task_id: str) -> 'SelfDriverConfig':
        business = Business.objects.get(productinitiative__engineering_tasks__id=task_id)
        task = Task.objects.get(id=task_id)
        base_file_name = common.safe_filename(task_id)

        self_driving_task, _ = SelfDrivingTask.objects.get_or_create(
            related_task_id=task_id,
            defaults={
                "main_name": base_file_name,
                "goal": task.get_work_desc(),
                "business": business
            }
        )

        business_sandbox_dir = business.get_sandbox_dir()

        initiative_dir_name = common.safe_filename(task.product_initiative_id)

        artifacts_root = business_sandbox_dir / ARTIFACTS / initiative_dir_name
        artifacts_root.mkdir(parents=True, exist_ok=True)

        code_root = business_sandbox_dir / initiative_dir_name
        code_root.mkdir(parents=True, exist_ok=True)

        log_file = artifacts_root / f"{base_file_name}.output.log"
        config_file = artifacts_root / f"{base_file_name}.config.json"
        main_code_path = code_root / f"{base_file_name}.py"

        return SelfDriverConfig({
            "sandbox_root_dir": business_sandbox_dir,
            "code_directory": code_root,
            "artifacts_dir": artifacts_root,
            "generate_single_file": True,
            "log_path": log_file,
            "supress_eval": True,
            "self_driving_task": self_driving_task,
        })

    def __init__(self, config):
        self.debug = False
        self.self_driving_task: SelfDrivingTask = config.get("self_driving_task")
        self.supress_eval = config.get("supress_eval", True)

        self.code_directory = config.get("code_directory")
        self.code_basename = self.self_driving_task.main_name

        self.main_code_file = CodeFile.get(
            self.code_directory / f"{self.code_basename}.py"
        )

        if self.self_driving_task.get_require_tests():
            self.main_code_file_test = CodeFile.get(
                self.code_directory / f"{self.code_basename}_test.py"
            )
        else:
            self.main_code_file_test = None

        self.generate_single_file = common.parse_bool(config.get("generate_single_file"))
        self.sandbox_root_dir = config.get("sandbox_root_dir")
        self.log_path = Path(config.get("log_path"))

        self.previous_iteration = self.self_driving_task.get_most_recent_iteration()
        self.current_iteration = None

        self.artifacts_dir = config.get("artifacts_dir")
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        self.is_model_trainer = common.parse_bool(config.get("is_model_trainer", "False"))

        self.attachments = common.get(config, "attachments", [])
        self.comment_requests = common.get(config, "comment_requests", [])

        self.never_done = common.parse_bool(config.get("never_done", False))
        self.max_budget_usd = float(config.get("max_budget_usd", 20))

        model_str = config.get("model")
        if model_str:
            self.model = random.choice(LlmModel.to_list(model_str.split(",")))
        else:
            self.model = random.choice(CODE_MODELS_IN_ORDER)

    def initialize_new_iteration(self) -> SelfDrivingTaskIteration:
        self.current_iteration = self.self_driving_task.iterate()
        return self.current_iteration
