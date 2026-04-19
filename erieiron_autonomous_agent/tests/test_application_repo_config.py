import uuid

import pytest

from erieiron_autonomous_agent.application_repo_config import (
    build_task_config_relative_path,
    build_workflow_config_relative_path,
    sync_business_application_repo,
)
from erieiron_autonomous_agent.models import (
    Business,
    Initiative,
    ProductRequirement,
    Task,
    WorkflowDefinition,
)
from erieiron_autonomous_agent.enums import TaskStatus
from erieiron_common import common
from erieiron_common.enums import (
    BusinessIdeaSource,
    InitiativeType,
    Level,
    PubSubMessageType,
    TaskExecutionSchedule,
    TaskType,
    WorkflowDefinitionSourceKind,
)


@pytest.mark.django_db
def test_sync_business_application_repo_imports_workflows_and_preserves_internal_definitions(
    tmp_path,
):
    erieiron_business = Business.get_erie_iron_business()
    stale_workflow = WorkflowDefinition.objects.create(
        name="Stale Application Workflow",
        source_kind=WorkflowDefinitionSourceKind.APPLICATION_REPO.value,
        is_active=True,
    )
    internal_workflow = WorkflowDefinition.objects.create(
        name="Internal Workflow",
        source_kind=WorkflowDefinitionSourceKind.ERIE_IRON_INTERNAL.value,
        is_active=True,
    )

    workflow_id = uuid.uuid4()
    start_step_id = uuid.uuid4()
    finish_step_id = uuid.uuid4()
    trigger_id = uuid.uuid4()
    connection_id = uuid.uuid4()
    config_path = tmp_path / build_workflow_config_relative_path(str(workflow_id))
    config_path.parent.mkdir(parents=True, exist_ok=True)
    common.write_json(
        config_path,
        {
            "schema_version": 1,
            "workflow_id": str(workflow_id),
            "name": "Imported Workflow",
            "description": "Imported from application repo",
            "is_active": True,
            "steps": [
                {
                    "id": str(start_step_id),
                    "name": "Start",
                    "handler_path": "erieiron_common.message_queue.pubsub_manager.PubSubManager.noop",
                    "emits_message_type": PubSubMessageType.ANALYSIS_REQUESTED.value,
                    "sort_order": 0,
                },
                {
                    "id": str(finish_step_id),
                    "name": "Finish",
                    "handler_path": "erieiron_common.message_queue.pubsub_manager.PubSubManager.noop",
                    "emits_message_type": None,
                    "sort_order": 1,
                },
            ],
            "triggers": [
                {
                    "id": str(trigger_id),
                    "target_step_id": str(start_step_id),
                    "message_type": PubSubMessageType.EVERY_MINUTE.value,
                    "sort_order": 0,
                }
            ],
            "connections": [
                {
                    "id": str(connection_id),
                    "source_step_id": str(start_step_id),
                    "target_step_id": str(finish_step_id),
                    "message_type": PubSubMessageType.ANALYSIS_REQUESTED.value,
                    "sort_order": 0,
                }
            ],
        },
    )

    result = sync_business_application_repo(
        erieiron_business,
        repo_root=tmp_path,
        pull=False,
        force=True,
    )

    imported_workflow = WorkflowDefinition.with_graph().get(id=workflow_id)
    assert imported_workflow.source_kind == WorkflowDefinitionSourceKind.APPLICATION_REPO.value
    assert imported_workflow.steps.count() == 2
    assert imported_workflow.triggers.count() == 1
    assert imported_workflow.connections.count() == 1
    assert not WorkflowDefinition.objects.filter(id=stale_workflow.id).exists()
    assert WorkflowDefinition.objects.filter(id=internal_workflow.id).exists()
    assert result["workflow_ids"] == [str(workflow_id)]


@pytest.mark.django_db
def test_sync_business_application_repo_replaces_legacy_internal_workflow_name_conflict(
    tmp_path,
):
    erieiron_business = Business.get_erie_iron_business()
    legacy_workflow = WorkflowDefinition.objects.create(
        name="Business Workflow",
        source_kind=WorkflowDefinitionSourceKind.ERIE_IRON_INTERNAL.value,
        is_active=True,
    )

    workflow_id = uuid.uuid4()
    start_step_id = uuid.uuid4()
    finish_step_id = uuid.uuid4()
    trigger_id = uuid.uuid4()
    connection_id = uuid.uuid4()
    config_path = tmp_path / build_workflow_config_relative_path(str(workflow_id))
    config_path.parent.mkdir(parents=True, exist_ok=True)
    common.write_json(
        config_path,
        {
            "schema_version": 1,
            "workflow_id": str(workflow_id),
            "name": "Business Workflow",
            "description": "Imported from application repo",
            "is_active": True,
            "steps": [
                {
                    "id": str(start_step_id),
                    "name": "Start",
                    "handler_path": "erieiron_common.message_queue.pubsub_manager.PubSubManager.noop",
                    "emits_message_type": PubSubMessageType.ANALYSIS_REQUESTED.value,
                    "sort_order": 0,
                },
                {
                    "id": str(finish_step_id),
                    "name": "Finish",
                    "handler_path": "erieiron_common.message_queue.pubsub_manager.PubSubManager.noop",
                    "emits_message_type": None,
                    "sort_order": 1,
                },
            ],
            "triggers": [
                {
                    "id": str(trigger_id),
                    "target_step_id": str(start_step_id),
                    "message_type": PubSubMessageType.EVERY_MINUTE.value,
                    "sort_order": 0,
                }
            ],
            "connections": [
                {
                    "id": str(connection_id),
                    "source_step_id": str(start_step_id),
                    "target_step_id": str(finish_step_id),
                    "message_type": PubSubMessageType.ANALYSIS_REQUESTED.value,
                    "sort_order": 0,
                }
            ],
        },
    )

    result = sync_business_application_repo(
        erieiron_business,
        repo_root=tmp_path,
        pull=False,
        force=True,
    )

    imported_workflow = WorkflowDefinition.with_graph().get(id=workflow_id)
    assert imported_workflow.name == "Business Workflow"
    assert imported_workflow.source_kind == WorkflowDefinitionSourceKind.APPLICATION_REPO.value
    assert imported_workflow.steps.count() == 2
    assert imported_workflow.triggers.count() == 1
    assert imported_workflow.connections.count() == 1
    assert not WorkflowDefinition.objects.filter(id=legacy_workflow.id).exists()
    assert result["workflow_ids"] == [str(workflow_id)]


@pytest.mark.django_db
def test_sync_business_application_repo_imports_task_bundle_config(tmp_path):
    business = Business.objects.create(
        name="Repo Config Business",
        source=BusinessIdeaSource.HUMAN,
        service_token="repo-config-business",
        github_repo_url="https://github.com/example/application-repo",
    )
    initiative = Initiative.objects.create(
        id="initiative-repo-config",
        business=business,
        title="Repo Config Initiative",
        description="Exercise task config sync",
        initiative_type=InitiativeType.ENGINEERING,
        priority=Level.MEDIUM,
    )
    dependency_task = Task.objects.create(
        id="task_dependency_repo_config",
        initiative=initiative,
        task_type=TaskType.HUMAN_WORK,
        status=TaskStatus.NOT_STARTED,
        description="Dependency",
        risk_notes="Dependency risk",
        completion_criteria=["Dependency complete"],
    )
    requirement = ProductRequirement.objects.create(
        id="requirement_repo_config",
        initiative=initiative,
        summary="Requirement summary",
        acceptance_criteria="Requirement acceptance",
        testable=True,
    )
    task = Task.objects.create(
        id="task_repo_config",
        initiative=initiative,
        task_type=TaskType.TASK_EXECUTION,
        status=TaskStatus.IN_PROGRESS,
        description="Stale description",
        risk_notes="Stale risk notes",
        completion_criteria=["Stale criteria"],
        execution_schedule=TaskExecutionSchedule.ONCE,
        prompt_improvement_schedule=TaskExecutionSchedule.NOT_APPLICABLE,
    )

    config_path = tmp_path / build_task_config_relative_path(task.id)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    common.write_json(
        config_path,
        {
            "schema_version": 1,
            "task_id": task.id,
            "initiative_id": str(initiative.id),
            "description": "Write task config",
            "status": TaskStatus.NOT_STARTED.value,
            "task_type": TaskType.TASK_EXECUTION.value,
            "risk_notes": "Keep runtime paths inside the repo",
            "completion_criteria": ["Task bundle exists"],
            "comment_requests": [],
            "attachments": [],
            "created_by": "system",
            "input_fields": {},
            "output_fields": ["answer"],
            "requires_test": True,
            "execution_schedule": TaskExecutionSchedule.ONCE.value,
            "prompt_improvement_schedule": TaskExecutionSchedule.DAILY.value,
            "execution_start_time": None,
            "timeout_seconds": None,
            "guidance": "Use the prompt from the repo bundle.",
            "max_budget_usd": None,
            "implementation_phase": None,
            "depends_on": [dependency_task.id],
            "validated_requirements": [requirement.id],
            "runtime": {
                "prompt_path": "prompt.md",
                "output_schema_path": "prompt.md.schema.json",
                "application_repo_ref": "main",
                "llm": {"model": "OPENAI_GPT_5_MINI"},
            },
            "evaluator": {"kind": "default"},
        },
    )
    (config_path.parent / "prompt.md").write_text(
        "Current system prompt",
        encoding="utf-8",
    )
    (config_path.parent / "prompt.md.schema.json").write_text(
        '{"type": "object"}',
        encoding="utf-8",
    )

    result = sync_business_application_repo(
        business,
        repo_root=tmp_path,
        pull=False,
        force=True,
    )

    task.refresh_from_db()
    active_version = task.get_active_implementation_version()

    assert task.description == "Write task config"
    assert task.risk_notes == "Keep runtime paths inside the repo"
    assert list(task.depends_on.values_list("id", flat=True)) == [dependency_task.id]
    assert list(task.validated_requirements.values_list("id", flat=True)) == [requirement.id]
    assert active_version.application_repo_file_path == build_task_config_relative_path(task.id)
    assert active_version.get_source_label().endswith("/prompt.md")
    assert active_version.get_prompt_text(tmp_path) == "Current system prompt"
    assert result["task_ids"] == [task.id]
