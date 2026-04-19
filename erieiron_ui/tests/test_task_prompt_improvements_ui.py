from unittest.mock import patch

import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from django.template.loader import render_to_string
from django.urls import reverse

from erieiron_autonomous_agent.enums import TaskStatus
from erieiron_autonomous_agent.models import (
    Business,
    Initiative,
    LlmRequest,
    Task,
    TaskPromptImprovement,
)
from erieiron_common.enums import (
    BusinessIdeaSource,
    InitiativeType,
    Level,
    TaskImplementationSourceKind,
    TaskPromptImprovementStatus,
    TaskPromptImprovementTrigger,
    TaskType,
)
from erieiron_ui import views


def _make_prompt_task() -> Task:
    business = Business.objects.create(
        name="UI Prompt Improvement Business",
        source=BusinessIdeaSource.HUMAN,
        service_token="ui-prompt-improvement-business",
        github_repo_url="https://github.com/example/application-repo",
    )
    initiative = Initiative.objects.create(
        id="ui-prompt-improvement-initiative",
        business=business,
        title="UI Prompt Improvement Initiative",
        description="Show prompt improvement review controls",
        initiative_type=InitiativeType.ENGINEERING,
        priority=Level.MEDIUM,
    )
    task = Task.objects.create(
        id="task_prompt_ui",
        initiative=initiative,
        task_type=TaskType.TASK_EXECUTION,
        status=TaskStatus.NOT_STARTED,
        description="Render task prompt improvements",
        risk_notes="Do not lose the active prompt",
        completion_criteria=["Prompt improvements are reviewable from the task view"],
        prompt_improvement_schedule="DAILY",
    )
    task.create_implementation_version(
        source_kind=TaskImplementationSourceKind.LLM_PROMPT,
        application_repo_file_path="prompts/task.md",
        application_repo_ref="main",
    )
    return task


@pytest.mark.django_db
def test_view_task_prompt_improvements_context_includes_candidates():
    task = _make_prompt_task()
    with patch(
        "erieiron_autonomous_agent.models.GitWrapper.get_commit_for_ref",
        return_value=("abc123", "latest commit"),
    ):
        execution = task.create_execution(model_metadata={"llm_model": "gpt-5.4"})
    execution.resolve(output={"ok": True}, evaluation_score=0.82)
    llm_request = LlmRequest.objects.create(
        business=task.initiative.business,
        initiative=task.initiative,
        task_iteration=None,
        token_count=0,
        price=0,
        title="Prompt improvement request",
    )
    TaskPromptImprovement.objects.create(
        task=task,
        base_implementation_version=task.get_active_implementation_version(),
        generated_llm_request=llm_request,
        status=TaskPromptImprovementStatus.PENDING_REVIEW,
        trigger_source=TaskPromptImprovementTrigger.MANUAL,
        context_json={"task_id": task.id},
        proposal_json={
            "summary": "Tighten the prompt around output format requirements.",
            "change_notes": ["Require the answer field explicitly."],
            "guardrails": ["Apply after a successful baseline run."],
            "rollback_signals": ["Rollback if the score trend drops."],
        },
        candidate_prompt_text="Improved prompt text",
    )
    request = RequestFactory().get(f"/task/prompt-improvements/{task.id}")

    with patch("erieiron_ui.views.send_response", return_value=HttpResponse("ok")) as mock_send_response:
        response = views.view_task(request, task.id, tab="prompt-improvements")

    assert response.status_code == 200
    context = mock_send_response.call_args.args[2]
    assert context["active_tab"] == "prompt-improvements"
    assert context["active_implementation_version"].source_label == "prompts/task.md"
    assert context["active_implementation_version"].evaluator_label == "Default"
    assert context["prompt_improvements"][0].summary == "Tighten the prompt around output format requirements."
    assert context["prompt_improvements"][0].generated_llm_request_url == reverse(
        "view_llm_request",
        args=[llm_request.id],
    )
    assert context["recent_prompt_improvement_executions"][0].display_model == "gpt-5.4"
    assert context["recent_prompt_improvement_average_score"] == pytest.approx(0.82)
    pending_review_summary = next(
        summary
        for summary in context["prompt_improvement_status_counts"]
        if summary["status"] == TaskPromptImprovementStatus.PENDING_REVIEW.value
    )
    assert pending_review_summary["count"] == 1


@pytest.mark.django_db
def test_apply_prompt_improvement_action_updates_active_version():
    task = _make_prompt_task()
    base_version = task.get_active_implementation_version()
    improvement = TaskPromptImprovement.objects.create(
        task=task,
        base_implementation_version=base_version,
        status=TaskPromptImprovementStatus.PENDING_REVIEW,
        trigger_source=TaskPromptImprovementTrigger.MANUAL,
        context_json={"task_id": task.id},
        proposal_json={
            "summary": "Tighten the prompt around output format requirements.",
            "change_notes": [],
            "guardrails": [],
            "rollback_signals": [],
        },
        candidate_prompt_text="Improved prompt text",
    )
    request = RequestFactory().post(
        reverse("action_apply_task_prompt_improvement", args=[improvement.id]),
        {"review_notes": "Apply from test"},
    )

    with patch("erieiron_ui.views.messages.success"):
        response = views.action_apply_task_prompt_improvement(request, improvement.id)

    improvement.refresh_from_db()
    task.refresh_from_db()
    assert response.status_code == 302
    assert improvement.status == TaskPromptImprovementStatus.APPLIED.value
    assert task.active_implementation_version_id != base_version.id
    assert task.active_implementation_version.source_metadata["prompt_override"] == "Improved prompt text"


@pytest.mark.django_db
def test_prompt_improvements_template_renders_review_briefing_sections():
    task = _make_prompt_task()
    with patch(
        "erieiron_autonomous_agent.models.GitWrapper.get_commit_for_ref",
        return_value=("abc123", "latest commit"),
    ):
        execution = task.create_execution(model_metadata={"llm_model": "gpt-5.4"})
    execution.resolve(output={"ok": True}, evaluation_score=0.76)
    TaskPromptImprovement.objects.create(
        task=task,
        base_implementation_version=task.get_active_implementation_version(),
        status=TaskPromptImprovementStatus.PENDING_REVIEW,
        trigger_source=TaskPromptImprovementTrigger.MANUAL,
        context_json={"task_id": task.id},
        proposal_json={
            "summary": "Refine the prompt with stricter response scaffolding.",
            "change_notes": ["Require a stable heading order."],
            "guardrails": ["Approve only after a stable run."],
            "rollback_signals": ["Rollback when average score drops."],
        },
        candidate_prompt_text="Candidate prompt text",
    )

    context = {
        "task": task,
        **views._task_tab_context_prompt_improvements(
            task,
            task.initiative.business,
            None,
        ),
    }

    html = render_to_string("task/tabs/prompt_improvements.html", context)

    assert "Current Prompt Loop" in html
    assert "Recent Eval Signals" in html
    assert "Prompt Proposals" in html
    assert "prompts/task.md" in html
    assert "gpt-5.4" in html
    assert "Require a stable heading order." in html
