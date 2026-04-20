import json
from datetime import datetime
from typing import Any

from django.core.exceptions import ValidationError

from erieiron_autonomous_agent.enums import TaskStatus
from erieiron_autonomous_agent.models import (
    Initiative,
    Task,
    WorkflowConnection,
    WorkflowDefinition,
    WorkflowStep,
    WorkflowTrigger,
)
from erieiron_common import common
from erieiron_common.enums import (
    PubSubMessageType,
    TaskExecutionSchedule,
    TaskImplementationPhase,
    TaskType,
    WorkflowDefinitionSourceKind,
)


def _normalize_workflow_message_type(message_type: str | None, *, required: bool) -> str | None:
    normalized_value = common.default_str(message_type).strip()
    if not normalized_value:
        if required:
            raise ValueError("Message type is required.")
        return None

    message_type_enum = PubSubMessageType.valid_or(normalized_value, None)
    if message_type_enum is None:
        raise ValueError(f"Invalid message type: {normalized_value}")

    return message_type_enum.value


def _normalize_workflow_sort_order(sort_order: Any) -> int:
    normalized_value = common.default_str(sort_order).strip()
    if not normalized_value:
        return 0

    sort_order_value = int(normalized_value)
    if sort_order_value < 0:
        raise ValueError("Sort order must be zero or greater.")

    return sort_order_value


def _get_application_workflow(workflow_id: str) -> WorkflowDefinition:
    return WorkflowDefinition.objects.get(
        pk=workflow_id,
        source_kind=WorkflowDefinitionSourceKind.APPLICATION_REPO.value,
    )


def _workflow_emitted_message_types(workflow: WorkflowDefinition) -> set[str]:
    return {
        step.emits_message_type
        for step in workflow.steps.all()
        if step.emits_message_type
    }


def save_workflow_definition(
        *,
        workflow_id: str | None,
        name: str,
        description: str | None,
        is_active: bool,
) -> WorkflowDefinition:
    workflow = (
        _get_application_workflow(workflow_id)
        if workflow_id
        else WorkflowDefinition(source_kind=WorkflowDefinitionSourceKind.APPLICATION_REPO.value)
    )
    workflow.name = common.default_str(name).strip()
    workflow.description = common.default_str(description).strip() or None
    workflow.is_active = is_active

    if not workflow.name:
        raise ValueError("Workflow name is required.")

    workflow.full_clean()
    workflow.save()
    return workflow


def delete_workflow_definition(workflow_id: str) -> None:
    _get_application_workflow(workflow_id).delete()


def save_workflow_step(
        *,
        workflow_id: str,
        step_id: str | None,
        name: str,
        handler_path: str,
        emits_message_type: str | None,
        sort_order: Any,
) -> WorkflowStep:
    workflow = _get_application_workflow(workflow_id)
    step = (
        WorkflowStep.objects.get(pk=step_id, workflow=workflow)
        if step_id
        else WorkflowStep(workflow=workflow)
    )
    step.name = common.default_str(name).strip()
    step.handler_path = common.default_str(handler_path).strip()
    step.emits_message_type = _normalize_workflow_message_type(
        emits_message_type,
        required=False,
    )
    step.sort_order = _normalize_workflow_sort_order(sort_order)

    if not step.name:
        raise ValueError("Step name is required.")
    if not step.handler_path:
        raise ValueError("Handler path is required.")

    step.full_clean()
    step.save()
    return step


def delete_workflow_step(step_id: str) -> None:
    WorkflowStep.objects.get(
        pk=step_id,
        workflow__source_kind=WorkflowDefinitionSourceKind.APPLICATION_REPO.value,
    ).delete()


def save_workflow_trigger(
        *,
        workflow_id: str,
        trigger_id: str | None,
        target_step_id: str,
        message_type: str,
        sort_order: Any,
) -> WorkflowTrigger:
    workflow = _get_application_workflow(workflow_id)
    emitted_message_types = _workflow_emitted_message_types(workflow)
    trigger = (
        WorkflowTrigger.objects.get(pk=trigger_id, workflow=workflow)
        if trigger_id
        else WorkflowTrigger(workflow=workflow)
    )
    trigger.target_step = WorkflowStep.objects.get(pk=target_step_id, workflow=workflow)
    trigger.message_type = _normalize_workflow_message_type(message_type, required=True)
    trigger.sort_order = _normalize_workflow_sort_order(sort_order)
    if trigger.message_type in emitted_message_types:
        raise ValueError(
            "External triggers must use a PubSub message type that is not emitted by a workflow step."
        )
    trigger.full_clean()
    trigger.save()
    return trigger


def delete_workflow_trigger(trigger_id: str) -> None:
    WorkflowTrigger.objects.get(
        pk=trigger_id,
        workflow__source_kind=WorkflowDefinitionSourceKind.APPLICATION_REPO.value,
    ).delete()


def save_workflow_connection(
        *,
        workflow_id: str,
        connection_id: str | None,
        source_step_id: str,
        target_step_id: str,
        message_type: str,
        sort_order: Any,
) -> WorkflowConnection:
    workflow = _get_application_workflow(workflow_id)
    connection = (
        WorkflowConnection.objects.get(pk=connection_id, workflow=workflow)
        if connection_id
        else WorkflowConnection(workflow=workflow)
    )
    connection.source_step = WorkflowStep.objects.get(pk=source_step_id, workflow=workflow)
    connection.target_step = WorkflowStep.objects.get(pk=target_step_id, workflow=workflow)
    connection.message_type = _normalize_workflow_message_type(message_type, required=True)
    connection.sort_order = _normalize_workflow_sort_order(sort_order)
    connection.full_clean()
    connection.save()
    return connection


def delete_workflow_connection(connection_id: str) -> None:
    WorkflowConnection.objects.get(
        pk=connection_id,
        workflow__source_kind=WorkflowDefinitionSourceKind.APPLICATION_REPO.value,
    ).delete()


def _normalize_task_type(task_type: str | None, *, default: TaskType) -> str:
    raw_value = common.default_str(task_type).strip()
    task_type_enum = TaskType.valid_or(raw_value, default)
    return task_type_enum.value


def _normalize_task_status(status: str | None, *, default: TaskStatus) -> str:
    raw_value = common.default_str(status).strip()
    status_enum = TaskStatus.valid_or(raw_value, default)
    return status_enum.value


def _normalize_task_schedule(
        schedule: str | None,
        *,
        field_name: str,
        default: TaskExecutionSchedule,
) -> str:
    raw_value = common.default_str(schedule).strip()
    schedule_enum = TaskExecutionSchedule.valid_or(raw_value, default)
    if schedule_enum is None:
        raise ValueError(f"Invalid {field_name}: {raw_value}")
    return schedule_enum.value


def _normalize_task_implementation_phase(implementation_phase: str | None) -> str | None:
    raw_value = common.default_str(implementation_phase).strip()
    if not raw_value:
        return None

    implementation_phase_enum = TaskImplementationPhase.valid_or(raw_value, None)
    if implementation_phase_enum is None:
        raise ValueError(f"Invalid implementation phase: {raw_value}")

    return implementation_phase_enum.value


def _normalize_task_completion_criteria(completion_criteria: Any) -> list[str]:
    if completion_criteria is None:
        return []

    normalized_value = completion_criteria
    if isinstance(completion_criteria, str):
        raw_value = completion_criteria.strip()
        if not raw_value:
            return []
        normalized_value = json.loads(raw_value)

    if not isinstance(normalized_value, list):
        raise ValueError("Completion criteria must be a JSON array.")

    return [
        str(item).strip()
        for item in normalized_value
        if str(item).strip()
    ]


def _normalize_optional_int(value: Any, *, field_name: str) -> int | None:
    raw_value = common.default_str(value).strip()
    if not raw_value:
        return None

    return int(raw_value)


def _normalize_optional_float(value: Any, *, field_name: str) -> float | None:
    raw_value = common.default_str(value).strip()
    if not raw_value:
        return None

    return float(raw_value)


def _normalize_optional_datetime(value: Any) -> datetime | None:
    raw_value = common.default_str(value).strip()
    if not raw_value:
        return None

    return datetime.fromisoformat(raw_value.replace("T", " "))


def create_task_from_structured_data(
        *,
        initiative: Initiative,
        task_id_token: str | None,
        description: str,
        completion_criteria: Any,
        risk_notes: str | None,
        task_type: str | None,
        requires_test: bool | None = None,
        status: str | None = None,
) -> Task:
    normalized_task_id_token = common.safe_filename(
        common.default_str(task_id_token).strip() or "task"
    ).strip("._-") or "task"
    normalized_completion_criteria = _normalize_task_completion_criteria(completion_criteria)
    normalized_requires_test = initiative.requires_unit_tests if requires_test is None else bool(requires_test)

    task = Task(
        id=f"{normalized_task_id_token}_{common.gen_random_token(8)}",
        initiative=initiative,
        task_type=_normalize_task_type(task_type, default=TaskType.HUMAN_WORK),
        status=_normalize_task_status(status, default=TaskStatus.NOT_STARTED),
        description=common.default_str(description).strip(),
        risk_notes=common.default_str(risk_notes).strip(),
        completion_criteria=normalized_completion_criteria or [
            "The task request has been fulfilled as described.",
        ],
        requires_test=normalized_requires_test,
    )
    task.full_clean()
    task.save()
    return task


def build_task_update_data(raw_data: dict[str, Any], *, partial: bool) -> dict[str, Any]:
    update_data: dict[str, Any] = {}
    if "description" in raw_data or not partial:
        update_data["description"] = common.default_str(raw_data.get("description")).strip()
    if "completion_criteria" in raw_data or not partial:
        update_data["completion_criteria"] = _normalize_task_completion_criteria(raw_data.get("completion_criteria"))
    if "risk_notes" in raw_data or not partial:
        update_data["risk_notes"] = common.default_str(raw_data.get("risk_notes")).strip()
    if "status" in raw_data or not partial:
        update_data["status"] = _normalize_task_status(raw_data.get("status"), default=TaskStatus.NOT_STARTED)
    if "task_type" in raw_data or not partial:
        update_data["task_type"] = _normalize_task_type(raw_data.get("task_type"), default=TaskType.HUMAN_WORK)
    if "execution_schedule" in raw_data or not partial:
        update_data["execution_schedule"] = _normalize_task_schedule(
            raw_data.get("execution_schedule"),
            field_name="execution schedule",
            default=TaskExecutionSchedule.ONCE,
        )
    if "prompt_improvement_schedule" in raw_data or not partial:
        update_data["prompt_improvement_schedule"] = _normalize_task_schedule(
            raw_data.get("prompt_improvement_schedule"),
            field_name="prompt improvement schedule",
            default=TaskExecutionSchedule.NOT_APPLICABLE,
        )
    if "requires_test" in raw_data or not partial:
        update_data["requires_test"] = bool(raw_data.get("requires_test"))
    if "timeout_seconds" in raw_data or not partial:
        update_data["timeout_seconds"] = _normalize_optional_int(
            raw_data.get("timeout_seconds"),
            field_name="timeout_seconds",
        )
    if "max_budget_usd" in raw_data or not partial:
        update_data["max_budget_usd"] = _normalize_optional_float(
            raw_data.get("max_budget_usd"),
            field_name="max_budget_usd",
        )
    if "execution_start_time" in raw_data or not partial:
        update_data["execution_start_time"] = _normalize_optional_datetime(
            raw_data.get("execution_start_time")
        )
    if "implementation_phase" in raw_data or not partial:
        update_data["implementation_phase"] = _normalize_task_implementation_phase(
            raw_data.get("implementation_phase")
        )

    return update_data


def update_task_from_data(
        *,
        task: Task,
        raw_data: dict[str, Any],
        partial: bool,
) -> Task:
    update_data = build_task_update_data(raw_data, partial=partial)
    for field_name, value in update_data.items():
        setattr(task, field_name, value)

    task.full_clean()
    task.save(update_fields=list(update_data.keys()))
    return task
