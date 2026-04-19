import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from erieiron_autonomous_agent.coding_agents import coding_agent
from erieiron_autonomous_agent.coding_agents.self_driving_coder_exceptions import GoalAchieved
from erieiron_autonomous_agent.models import (
    Business,
    Initiative,
    SelfDrivingTask,
    SelfDrivingTaskIteration,
    Task,
)
from erieiron_autonomous_agent.enums import TaskStatus
from erieiron_common.enums import (
    BusinessIdeaSource,
    InitiativeType,
    Level,
    LlmModel,
    TaskImplementationSourceKind,
    TaskType,
)


class _DummyLlmResponse:
    def __init__(self, payload):
        self._payload = payload
        self.text = json.dumps(payload)
        self.model = LlmModel.OPENAI_GPT_5_MINI

    def json(self):
        return dict(self._payload)


class _StubGit:
    def __init__(self, sandbox_root: Path, output_payload=None):
        self.sandbox_root = Path(sandbox_root)
        self.output_payload = output_payload or {}
        self.checked_out_refs = []
        self.python_commands = []

    def checkout_ref(self, repo_ref: str) -> str:
        self.checked_out_refs.append(repo_ref)
        return "runtime123"

    def run_python(self, cmd, env):
        command = [str(part) for part in cmd]
        self.python_commands.append(command)
        output_file = Path(command[command.index("--output_file") + 1])
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(self.output_payload))
        return SimpleNamespace(stdout="ok", stderr="", returncode=0)

    def cleanup(self):
        return None


class _StubConfig:
    def __init__(self, *, task, self_driving_task, iteration, sandbox_root_dir, git):
        self.task = task
        self.task_type = TaskType(task.task_type)
        self.self_driving_task = self_driving_task
        self.current_iteration = iteration
        self.sandbox_root_dir = Path(sandbox_root_dir)
        self.runtime_env = {}
        self.git = git
        self.logs = []
        self.phase = None

    def log(self, *args):
        self.logs.append(" ".join(str(arg) for arg in args))

    def dump_env_to_envrc(self):
        return None

    def set_phase(self, phase):
        self.phase = phase
        return ""


def _make_task(task_id: str) -> Task:
    business = Business.objects.create(
        name=f"Repo Execution Business {task_id}",
        source=BusinessIdeaSource.HUMAN,
        service_token=f"repo-exec-{task_id}",
        github_repo_url="https://github.com/example/application-repo",
    )
    initiative = Initiative.objects.create(
        id=f"initiative-{task_id}",
        business=business,
        title="Repo Execution Initiative",
        description="Exercise repo-backed runtime execution",
        initiative_type=InitiativeType.ENGINEERING,
        priority=Level.MEDIUM,
    )
    return Task.objects.create(
        id=task_id,
        initiative=initiative,
        task_type=TaskType.TASK_EXECUTION,
        status=TaskStatus.NOT_STARTED,
        description="Run a repo-backed task implementation",
        risk_notes="Use the application repo implementation directly",
        completion_criteria=["Task implementation executes successfully"],
        output_fields=["answer"],
    )


@pytest.mark.django_db
def test_execute_iteration_repo_backed_prompt_task_marks_goal_achieved(tmp_path):
    task = _make_task("repo_prompt_task")
    task.create_implementation_version(
        source_kind=TaskImplementationSourceKind.LLM_PROMPT,
        application_repo_file_path="prompts/run_task.md",
        application_repo_ref="main",
    )
    self_driving_task = SelfDrivingTask.objects.create(
        business=task.initiative.business,
        main_name=task.id,
        sandbox_path=str(tmp_path),
        goal="Run prompt-backed task",
        task=task,
    )
    iteration = SelfDrivingTaskIteration.objects.create(
        self_driving_task=self_driving_task,
        version_number=1,
        planning_model="gpt-5.4",
        coding_model="gpt-5.4-mini",
    )
    with patch(
        "erieiron_autonomous_agent.models.GitWrapper.get_commit_for_ref",
        return_value=("seed123", "seed"),
    ):
        task_execution = task.create_execution(iteration=iteration)

    prompt_path = tmp_path / "prompts" / "run_task.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text("Return the answer field.")
    config = _StubConfig(
        task=task,
        self_driving_task=self_driving_task,
        iteration=iteration,
        sandbox_root_dir=tmp_path,
        git=_StubGit(tmp_path),
    )

    with patch.object(coding_agent, "ingest_tofu_ouputs", return_value=None), patch.object(
        coding_agent,
        "llm_chat",
        return_value=_DummyLlmResponse({"answer": "done"}),
    ):
        with pytest.raises(GoalAchieved):
            coding_agent.execute_iteration(config, task_execution=task_execution)

    task_execution.refresh_from_db()
    iteration.refresh_from_db()
    assert task_execution.status == TaskStatus.COMPLETE
    assert task_execution.output == {"answer": "done"}
    assert task_execution.model_metadata["llm_model"] == LlmModel.OPENAI_GPT_5_MINI.value
    assert task_execution.implementation_provenance["application_repo_revision"] == "runtime123"
    assert iteration.evaluation_json["goal_achieved"] is True


@pytest.mark.django_db
def test_execute_iteration_repo_backed_code_task_runs_management_command(tmp_path):
    task = _make_task("repo_code_task")
    task.create_implementation_version(
        source_kind=TaskImplementationSourceKind.CODE_FILE,
        application_repo_file_path="app/management/commands/run_repo_task.py",
        application_repo_ref="main",
    )
    self_driving_task = SelfDrivingTask.objects.create(
        business=task.initiative.business,
        main_name=task.id,
        sandbox_path=str(tmp_path),
        goal="Run code-backed task",
        task=task,
    )
    iteration = SelfDrivingTaskIteration.objects.create(
        self_driving_task=self_driving_task,
        version_number=1,
        planning_model="gpt-5.4",
        coding_model="gpt-5.4-mini",
    )
    with patch(
        "erieiron_autonomous_agent.models.GitWrapper.get_commit_for_ref",
        return_value=("seed123", "seed"),
    ):
        task_execution = task.create_execution(iteration=iteration)

    command_path = tmp_path / "app" / "management" / "commands" / "run_repo_task.py"
    command_path.parent.mkdir(parents=True, exist_ok=True)
    command_path.write_text("print('repo command')")
    git = _StubGit(tmp_path, output_payload={"answer": "from-code"})
    config = _StubConfig(
        task=task,
        self_driving_task=self_driving_task,
        iteration=iteration,
        sandbox_root_dir=tmp_path,
        git=git,
    )

    with patch.object(coding_agent, "ingest_tofu_ouputs", return_value=None):
        with pytest.raises(GoalAchieved):
            coding_agent.execute_iteration(config, task_execution=task_execution)

    task_execution.refresh_from_db()
    assert git.python_commands[0][:2] == ["manage.py", "run_repo_task"]
    assert task_execution.status == TaskStatus.COMPLETE
    assert task_execution.output == {"answer": "from-code"}
