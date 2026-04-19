from types import SimpleNamespace
from unittest.mock import patch

import pytest

from erieiron_autonomous_agent.management.commands.exec_prompt_improvements import Command
from erieiron_autonomous_agent.models import (
    AgentLesson,
    Business,
    Initiative,
    LlmRequest,
    SelfDrivingTask,
    SelfDrivingTaskIteration,
    Task,
    TaskPromptImprovement,
)
from erieiron_autonomous_agent.enums import TaskStatus
from erieiron_common.enums import (
    BusinessIdeaSource,
    InitiativeType,
    Level,
    TaskExecutionSchedule,
    TaskImplementationSourceKind,
    TaskPromptImprovementStatus,
    TaskPromptImprovementTrigger,
    TaskType,
)


class _DummyLlmResponse:
    def __init__(self, payload, llm_request_id="11111111-1111-1111-1111-111111111111"):
        self._payload = payload
        self.llm_request_id = llm_request_id

    def json(self):
        return dict(self._payload)


def _make_prompt_task(task_id: str = "task_prompt_improvement") -> tuple[Task, SelfDrivingTask]:
    business = Business.objects.create(
        name=f"Prompt Improvement Business {task_id}",
        source=BusinessIdeaSource.HUMAN,
        service_token=f"prompt-improvement-{task_id}",
        github_repo_url="https://github.com/example/application-repo",
    )
    initiative = Initiative.objects.create(
        id=f"initiative-{task_id}",
        business=business,
        title="Prompt Improvement Initiative",
        description="Improve a prompt-backed task",
        initiative_type=InitiativeType.ENGINEERING,
        priority=Level.MEDIUM,
    )
    task = Task.objects.create(
        id=task_id,
        initiative=initiative,
        task_type=TaskType.TASK_EXECUTION,
        status=TaskStatus.NOT_STARTED,
        description="Run a prompt-backed task",
        risk_notes="Keep prompt provenance stable",
        completion_criteria=["Task output remains valid"],
    )
    task.create_implementation_version(
        source_kind=TaskImplementationSourceKind.LLM_PROMPT,
        application_repo_file_path="prompts/task.md",
        application_repo_ref="main",
        source_metadata={"model": "OPENAI_GPT_5_MINI"},
    )
    self_driving_task = SelfDrivingTask.objects.create(
        business=business,
        main_name=task.id,
        sandbox_path="/tmp/prompt-improvement-sandbox",
        goal="Run prompt-backed task",
        task=task,
    )
    return task, self_driving_task


def _create_execution(task: Task, self_driving_task: SelfDrivingTask, version_number: int, score: float):
    iteration = SelfDrivingTaskIteration.objects.create(
        self_driving_task=self_driving_task,
        version_number=version_number,
        planning_model="gpt-5.4",
        coding_model="gpt-5.4-mini",
    )
    with patch(
        "erieiron_autonomous_agent.models.GitWrapper.get_commit_for_ref",
        return_value=("abc123", "latest commit"),
    ):
        execution = task.create_execution(iteration=iteration)
    execution.resolve(
        output={"result": f"run-{version_number}"},
        evaluation_score=score,
        evaluation_metadata={"score_source": "test"},
    )
    return iteration, execution


@pytest.mark.django_db
def test_build_prompt_improvement_context_includes_executions_and_lessons(tmp_path):
    task, self_driving_task = _make_prompt_task()
    prompt_file = tmp_path / "prompts" / "task.md"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text("Current system prompt", encoding="utf-8")

    iteration, _ = _create_execution(task, self_driving_task, version_number=1, score=0.25)
    AgentLesson.objects.create(
        source_iteration=iteration,
        agent_step="TASK_EXECUTION",
        pattern="Too vague",
        trigger="Output missed required detail",
        lesson="Be explicit about required fields",
        context_tags=["prompt"],
    )

    with patch.object(
        Task,
        "create_self_driving_env",
        return_value=SimpleNamespace(sandbox_path=str(tmp_path)),
    ):
        context = task.build_prompt_improvement_context()

    assert context["active_implementation_version"]["prompt_text"] == "Current system prompt"
    assert len(context["recent_executions"]) == 1
    assert context["recent_executions"][0]["evaluation_score"] == 0.25
    assert context["lessons"][0]["lesson"] == "Be explicit about required fields"


@pytest.mark.django_db
def test_generate_prompt_improvement_candidate_persists_pending_review(tmp_path):
    task, self_driving_task = _make_prompt_task("task_generate_prompt_improvement")
    prompt_file = tmp_path / "prompts" / "task.md"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text("Current prompt text", encoding="utf-8")
    _create_execution(task, self_driving_task, version_number=1, score=0.4)
    llm_request = LlmRequest.objects.create(
        business=task.initiative.business,
        initiative=task.initiative,
        task_iteration=None,
        token_count=0,
        price=0,
        title="Prompt improvement request",
    )

    with patch.object(
        Task,
        "create_self_driving_env",
        return_value=SimpleNamespace(sandbox_path=str(tmp_path)),
    ), patch(
        "erieiron_autonomous_agent.system_agent_llm_interface.llm_chat",
        return_value=_DummyLlmResponse(
            {
                "summary": "Tighten instructions around required output fields.",
                "candidate_prompt_markdown": "Improved prompt text",
                "change_notes": ["Require the output fields explicitly."],
                "guardrails": ["Only apply after at least one successful run."],
                "rollback_signals": ["Average score drops below the previous baseline."],
            },
            llm_request_id=str(llm_request.id),
        ),
    ):
        improvement = task.generate_prompt_improvement_candidate()

    task.refresh_from_db()
    assert improvement.status == TaskPromptImprovementStatus.PENDING_REVIEW.value
    assert improvement.generated_llm_request_id == llm_request.id
    assert improvement.candidate_prompt_text == "Improved prompt text"
    assert task.last_prompt_improvement_at is not None


@pytest.mark.django_db
def test_apply_and_rollback_prompt_improvement_updates_active_version():
    task, _ = _make_prompt_task("task_apply_prompt_improvement")
    base_version = task.get_active_implementation_version()
    improvement = TaskPromptImprovement.objects.create(
        task=task,
        base_implementation_version=base_version,
        status=TaskPromptImprovementStatus.PENDING_REVIEW,
        trigger_source=TaskPromptImprovementTrigger.MANUAL,
        context_json={"task_id": task.id},
        proposal_json={
            "summary": "Improve the task prompt",
            "change_notes": [],
            "guardrails": [],
            "rollback_signals": [],
        },
        candidate_prompt_text="Generated prompt text",
    )

    applied_version = improvement.apply(review_notes="Apply the candidate")
    task.refresh_from_db()
    improvement.refresh_from_db()

    assert task.active_implementation_version_id == applied_version.id
    assert applied_version.version_number == 2
    assert applied_version.source_metadata["prompt_override"] == "Generated prompt text"
    assert improvement.status == TaskPromptImprovementStatus.APPLIED.value

    improvement.rollback(review_notes="Roll back the candidate")
    task.refresh_from_db()
    improvement.refresh_from_db()

    assert task.active_implementation_version_id == base_version.id
    assert improvement.status == TaskPromptImprovementStatus.ROLLED_BACK.value


@pytest.mark.django_db
def test_exec_prompt_improvements_only_targets_due_tasks():
    due_task, due_self_driving_task = _make_prompt_task("task_due_prompt_improvement")
    not_due_task, not_due_self_driving_task = _make_prompt_task("task_not_due_prompt_improvement")

    due_task.prompt_improvement_schedule = TaskExecutionSchedule.DAILY
    due_task.save(update_fields=["prompt_improvement_schedule"])
    not_due_task.prompt_improvement_schedule = TaskExecutionSchedule.DAILY
    not_due_task.save(update_fields=["prompt_improvement_schedule"])

    for version_number in range(1, 4):
        _create_execution(due_task, due_self_driving_task, version_number=version_number, score=0.8)
    _create_execution(not_due_task, not_due_self_driving_task, version_number=1, score=0.8)

    command = Command()
    with patch.object(
        Task,
        "generate_prompt_improvement_candidate",
        autospec=True,
        return_value=SimpleNamespace(id="generated-improvement"),
    ) as generate_mock:
        command.handle()

    generate_mock.assert_called_once()
    called_task = generate_mock.call_args.args[0]
    assert called_task.id == due_task.id
    assert generate_mock.call_args.kwargs["trigger_source"] == TaskPromptImprovementTrigger.SCHEDULED
