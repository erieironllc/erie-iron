import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError

from erieiron_autonomous_agent.coding_agents import coding_agent
from erieiron_autonomous_agent.enums import TaskStatus
from erieiron_autonomous_agent.models import (
    Business,
    Initiative,
    SelfDrivingTask,
    SelfDrivingTaskIteration,
    Task,
)
from erieiron_common.enums import (
    BusinessIdeaSource,
    InitiativeType,
    Level,
    LlmModel,
    TaskImplementationSourceKind,
    TaskType,
)


def _make_task() -> Task:
    business = Business.objects.create(
        name="Implementation Test Business",
        source=BusinessIdeaSource.HUMAN,
        service_token="impl-test-business",
        github_repo_url="https://github.com/example/application-repo",
    )
    initiative = Initiative.objects.create(
        id="impl-initiative",
        business=business,
        title="Implementation Initiative",
        description="Exercise implementation version storage",
        initiative_type=InitiativeType.ENGINEERING,
        priority=Level.MEDIUM,
    )
    return Task.objects.create(
        id="task_impl_versions",
        initiative=initiative,
        task_type=TaskType.CODING_APPLICATION,
        status=TaskStatus.NOT_STARTED,
        description="Track task implementation sources",
        risk_notes="Keep provenance stable",
        completion_criteria=["Implementation versions persist"],
    )


class _DummyLlmResponse:
    def __init__(self, payload):
        self._payload = payload
        self.text = json.dumps(payload)
        self.model = LlmModel.OPENAI_GPT_5_MINI

    def json(self):
        return dict(self._payload)


@pytest.mark.django_db
def test_create_implementation_version_sets_task_kind_and_active_version():
    task = _make_task()

    first_version = task.create_implementation_version(
        source_kind=TaskImplementationSourceKind.LLM_PROMPT,
        application_repo_file_path="prompts/run_task.md",
        application_repo_ref="main",
    )
    second_version = task.create_implementation_version(
        application_repo_file_path="prompts/run_task_v2.md",
        application_repo_ref="main",
    )

    task.refresh_from_db()

    assert task.implementation_source_kind == TaskImplementationSourceKind.LLM_PROMPT.value
    assert task.active_implementation_version_id == second_version.id
    assert [version.version_number for version in task.get_implementation_versions()] == [2, 1]
    assert first_version.version_number == 1
    assert second_version.version_number == 2


@pytest.mark.django_db
def test_create_execution_snapshots_code_file_provenance_and_iteration_models(tmp_path):
    task = _make_task()
    version = task.create_implementation_version(
        source_kind=TaskImplementationSourceKind.CODE_FILE,
        application_repo_file_path="tasks/run_task.py",
        application_repo_ref="main",
        evaluator_config={
            "application_repo_file_path": "tasks/evaluate_task.py",
            "application_repo_ref": "main",
        },
    )
    self_driving_task = SelfDrivingTask.objects.create(
        business=task.initiative.business,
        main_name="task_impl_versions",
        sandbox_path=str(tmp_path / "sandbox"),
        goal="Run the task",
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
        return_value=("abc123", "latest commit"),
    ) as mock_get_commit_for_ref:
        execution = task.create_execution(iteration=iteration)
    execution.resolve(output={"status": "ok"})

    assert execution.implementation_version_id == version.id
    assert execution.implementation_source_kind == TaskImplementationSourceKind.CODE_FILE.value
    assert execution.implementation_provenance == {
        "source_version_number": 1,
        "source_kind": TaskImplementationSourceKind.CODE_FILE.value,
        "application_repo_file_path": "tasks/run_task.py",
        "application_repo_ref": "main",
        "instance_config_revision": "abc123",
        "source_metadata": {},
        "application_repo_url": "https://github.com/example/application-repo",
        "application_repo_revision": "abc123",
    }
    assert execution.model_metadata == {
        "planning_model": "gpt-5.4",
        "coding_model": "gpt-5.4-mini",
    }
    assert execution.evaluation_metadata["evaluator"] == {
        "kind": TaskImplementationSourceKind.CODE_FILE.value,
        "config": {
            "application_repo_file_path": "tasks/evaluate_task.py",
            "application_repo_ref": "main",
        },
    }
    mock_get_commit_for_ref.assert_called_once_with("https://github.com/example/application-repo", "main")


@pytest.mark.django_db
def test_create_execution_snapshots_prompt_file_provenance():
    task = _make_task()
    version = task.create_implementation_version(
        source_kind=TaskImplementationSourceKind.LLM_PROMPT,
        application_repo_file_path="prompts/run_task.md",
        application_repo_ref="main",
    )

    with patch(
        "erieiron_autonomous_agent.models.GitWrapper.get_commit_for_ref",
        return_value=("abc123", "latest commit"),
    ) as mock_get_commit_for_ref:
        execution = task.create_execution()

    assert execution.implementation_version_id == version.id
    assert execution.implementation_source_kind == TaskImplementationSourceKind.LLM_PROMPT.value
    assert execution.implementation_provenance == {
        "source_version_number": 1,
        "source_kind": TaskImplementationSourceKind.LLM_PROMPT.value,
        "application_repo_file_path": "prompts/run_task.md",
        "application_repo_ref": "main",
        "instance_config_revision": "abc123",
        "source_metadata": {},
        "application_repo_url": "https://github.com/example/application-repo",
        "application_repo_revision": "abc123",
    }
    mock_get_commit_for_ref.assert_called_once_with("https://github.com/example/application-repo", "main")


@pytest.mark.django_db
def test_run_repo_backed_prompt_task_prefers_prompt_override(tmp_path):
    task = _make_task()
    Task.objects.filter(id=task.id).update(output_fields=["answer"])
    task.refresh_from_db(fields=["output_fields"])
    version = task.create_implementation_version(
        source_kind=TaskImplementationSourceKind.LLM_PROMPT,
        application_repo_file_path="prompts/run_task.md",
        application_repo_ref="main",
        source_metadata={"prompt_override": "Use the approved override prompt."},
    )
    self_driving_task = SelfDrivingTask.objects.create(
        business=task.initiative.business,
        main_name="task_impl_versions",
        sandbox_path=str(tmp_path / "sandbox"),
        goal="Run the task",
        task=task,
    )
    iteration = SelfDrivingTaskIteration.objects.create(
        self_driving_task=self_driving_task,
        version_number=1,
        planning_model="gpt-5.4",
        coding_model="gpt-5.4-mini",
    )
    prompt_path = tmp_path / "prompts" / "run_task.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text("Stale prompt text", encoding="utf-8")

    with patch(
        "erieiron_autonomous_agent.models.GitWrapper.get_commit_for_ref",
        return_value=("abc123", "latest commit"),
    ):
        task_execution = task.create_execution(iteration=iteration)

    config = SimpleNamespace(
        task=task,
        current_iteration=iteration,
        sandbox_root_dir=Path(tmp_path),
    )

    with patch.object(
        coding_agent,
        "llm_chat",
        return_value=_DummyLlmResponse({"answer": "ok"}),
    ) as mock_llm_chat:
        output_data, model_metadata = coding_agent._run_repo_backed_prompt_task(
            config,
            task_execution,
            version,
        )

    assert output_data == {"answer": "ok"}
    assert model_metadata["prompt_file_path"] == "prompts/run_task.md"
    assert mock_llm_chat.call_args.args[1][0].text == "Use the approved override prompt."


@pytest.mark.django_db
def test_task_execution_resolve_defaults_score_by_crash_state():
    task = _make_task()

    successful_execution = task.create_execution()
    successful_execution.resolve(output={"status": "ok"})

    failed_execution = task.create_execution()
    failed_execution.resolve(status=TaskStatus.FAILED, error_msg="boom")

    assert successful_execution.evaluation_score == 1.0
    assert successful_execution.evaluation_metadata["evaluator"] == {
        "kind": "default",
        "config": {},
    }
    assert failed_execution.evaluation_score == 0.0
    assert failed_execution.evaluation_metadata["evaluator"] == {
        "kind": "default",
        "config": {},
    }


@pytest.mark.django_db
def test_prompt_implementations_and_custom_evaluators_require_repo_pointers():
    task = _make_task()

    with pytest.raises(ValidationError, match="Llm prompt implementations require application_repo_file_path"):
        task.create_implementation_version(
            source_kind=TaskImplementationSourceKind.LLM_PROMPT,
        )

    with pytest.raises(ValidationError, match="custom evaluators require application_repo_file_path"):
        task.create_implementation_version(
            source_kind=TaskImplementationSourceKind.LLM_PROMPT,
            application_repo_file_path="prompts/run_task.md",
            application_repo_ref="main",
            evaluator_config={"notes": "missing repo pointer"},
        )
