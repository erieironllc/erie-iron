from unittest.mock import patch

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from erieiron_autonomous_agent.enums import TaskStatus
from erieiron_autonomous_agent.models import Business, Initiative, Task
from erieiron_common.enums import (
    BusinessIdeaSource,
    InitiativeType,
    Level,
    TaskImplementationSourceKind,
    TaskType,
)
from erieiron_ui import views


def _make_task() -> Task:
    business = Business.objects.create(
        name="UI Implementation Business",
        source=BusinessIdeaSource.HUMAN,
        service_token="ui-implementation-business",
        github_repo_url="https://github.com/example/application-repo",
    )
    initiative = Initiative.objects.create(
        id="ui-implementation-initiative",
        business=business,
        title="UI Implementation Initiative",
        description="Expose task implementation history",
        initiative_type=InitiativeType.ENGINEERING,
        priority=Level.MEDIUM,
    )
    return Task.objects.create(
        id="task_impl_ui",
        initiative=initiative,
        task_type=TaskType.CODING_APPLICATION,
        status=TaskStatus.NOT_STARTED,
        description="Render task implementation history",
        risk_notes="Do not hide provenance",
        completion_criteria=["Task view exposes implementation history"],
    )


@pytest.mark.django_db
def test_view_task_overview_context_includes_active_implementation_history():
    task = _make_task()
    task.create_implementation_version(
        source_kind=TaskImplementationSourceKind.LLM_PROMPT,
        application_repo_file_path="prompts/task_v1.md",
        application_repo_ref="main",
    )
    active_version = task.create_implementation_version(
        application_repo_file_path="prompts/task_v2.md",
        application_repo_ref="main",
    )
    request = RequestFactory().get(f"/task/{task.id}/")

    with patch("erieiron_ui.views.send_response", return_value=HttpResponse("ok")) as mock_send_response:
        response = views.view_task(request, task.id, tab="overview")

    assert response.status_code == 200
    context = mock_send_response.call_args.args[2]
    assert context["active_implementation_version"].id == active_version.id
    assert context["active_implementation_version"].source_label == "prompts/task_v2.md"
    assert context["active_implementation_version"].evaluator_label == "Default"
    assert [version.version_number for version in context["implementation_versions"]] == [2, 1]
    assert context["tab_template"] == "task/tabs/overview.html"


@pytest.mark.django_db
def test_view_task_executions_context_includes_execution_audit_details():
    task = _make_task()
    task.create_implementation_version(
        source_kind=TaskImplementationSourceKind.CODE_FILE,
        application_repo_file_path="tasks/run_task.py",
        application_repo_ref="main",
    )
    with patch(
        "erieiron_autonomous_agent.models.GitWrapper.get_commit_for_ref",
        return_value=("abc123", "latest commit"),
    ):
        execution = task.create_execution(model_metadata={"llm_model": "gpt-5.4"})
    execution.resolve(output={"ok": True})

    request = RequestFactory().get(f"/task/{task.id}/executions/")

    with patch("erieiron_ui.views.send_response", return_value=HttpResponse("ok")) as mock_send_response:
        response = views.view_task(request, task.id, tab="executions")

    assert response.status_code == 200
    context = mock_send_response.call_args.args[2]
    assert context["active_tab"] == "executions"
    assert len(context["task_executions"]) == 1
    rendered_execution = context["task_executions"][0]
    assert rendered_execution.implementation_source_kind == TaskImplementationSourceKind.CODE_FILE.value
    assert rendered_execution.implementation_provenance["application_repo_revision"] == "abc123"
    assert rendered_execution.model_metadata == {"llm_model": "gpt-5.4"}
