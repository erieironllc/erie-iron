import pytest
from unittest.mock import patch

from erieiron_autonomous_agent.models import (
    WorkflowConnection,
    WorkflowDefinition,
    WorkflowStep,
    WorkflowTrigger,
)
from erieiron_autonomous_agent.workflow import initialize_database_workflows
from erieiron_common.enums import PubSubMessageType
from erieiron_common.llm_request_handler import handle_llm_request
from erieiron_common.message_queue.pubsub_manager import PubSubManager, subscribers


def initial_handler(payload):
    return payload


def downstream_handler(payload):
    return payload


@pytest.fixture(autouse=True)
def clear_subscribers():
    subscribers.clear()
    yield
    subscribers.clear()


@pytest.mark.django_db
def test_initialize_database_workflows_registers_active_workflow_edges():
    workflow = WorkflowDefinition.objects.create(name="Custom Runtime Workflow", is_active=True)
    first_step = WorkflowStep.objects.create(
        workflow=workflow,
        name="Find opportunities",
        handler_path=f"{__name__}.initial_handler",
        emits_message_type=PubSubMessageType.EVERY_HOUR.value,
        sort_order=0,
    )
    second_step = WorkflowStep.objects.create(
        workflow=workflow,
        name="Submit opportunities",
        handler_path=f"{__name__}.downstream_handler",
        emits_message_type=PubSubMessageType.EVERY_DAY.value,
        sort_order=1,
    )

    WorkflowTrigger.objects.create(
        workflow=workflow,
        target_step=first_step,
        message_type=PubSubMessageType.EVERY_MINUTE.value,
        sort_order=0,
    )
    WorkflowConnection.objects.create(
        workflow=workflow,
        source_step=first_step,
        target_step=second_step,
        message_type=PubSubMessageType.EVERY_HOUR.value,
        sort_order=0,
    )

    with patch(
        "erieiron_autonomous_agent.workflow.sync_erieiron_application_repo_if_changed"
    ):
        initialize_database_workflows(PubSubManager())

    assert (
        initial_handler,
        None,
        PubSubMessageType.EVERY_HOUR,
    ) in subscribers[PubSubMessageType.EVERY_MINUTE]
    assert (
        downstream_handler,
        None,
        PubSubMessageType.EVERY_DAY,
    ) in subscribers[PubSubMessageType.EVERY_HOUR]


@pytest.mark.django_db
def test_initialize_database_workflows_syncs_internal_and_application_workflows_before_registering():
    workflow = WorkflowDefinition.objects.create(name="Synced Runtime Workflow", is_active=True)
    step = WorkflowStep.objects.create(
        workflow=workflow,
        name="Find opportunities",
        handler_path=f"{__name__}.initial_handler",
        emits_message_type=PubSubMessageType.EVERY_HOUR.value,
    )
    WorkflowTrigger.objects.create(
        workflow=workflow,
        target_step=step,
        message_type=PubSubMessageType.EVERY_MINUTE.value,
    )

    sync_calls = []
    with patch(
        "erieiron_autonomous_agent.workflow.sync_internal_workflows",
        side_effect=lambda: sync_calls.append("internal"),
    ) as internal_sync_mock, patch(
        "erieiron_autonomous_agent.workflow.sync_erieiron_application_repo_if_changed",
        side_effect=lambda: sync_calls.append("application_repo"),
    ) as sync_mock:
        initialize_database_workflows(PubSubManager())

    internal_sync_mock.assert_called_once_with()
    sync_mock.assert_called_once_with()
    assert sync_calls == ["internal", "application_repo"]


@pytest.mark.django_db
def test_initialize_database_workflows_skips_inactive_workflows():
    workflow = WorkflowDefinition.objects.create(name="Inactive Runtime Workflow", is_active=False)
    step = WorkflowStep.objects.create(
        workflow=workflow,
        name="Find opportunities",
        handler_path=f"{__name__}.initial_handler",
        emits_message_type=PubSubMessageType.EVERY_HOUR.value,
    )
    WorkflowTrigger.objects.create(
        workflow=workflow,
        target_step=step,
        message_type=PubSubMessageType.EVERY_MINUTE.value,
    )

    with patch(
        "erieiron_autonomous_agent.workflow.sync_erieiron_application_repo_if_changed"
    ):
        initialize_database_workflows(PubSubManager())

    assert not subscribers[PubSubMessageType.EVERY_MINUTE]


@pytest.mark.django_db
def test_workflow_step_resolves_nested_handler_paths():
    workflow = WorkflowDefinition.objects.create(name="Custom Nested Handler Workflow", is_active=True)
    step = WorkflowStep.objects.create(
        workflow=workflow,
        name="Queue product initiatives",
        handler_path="erieiron_common.message_queue.pubsub_manager.PubSubManager.noop",
        emits_message_type=PubSubMessageType.PRODUCT_INITIATIVES_REQUESTED.value,
    )

    handler = step.get_handler()

    assert handler.__self__ is PubSubManager
    assert handler.__name__ == "noop"


@pytest.mark.django_db
def test_initialize_database_workflows_registers_internal_llm_request_handler():
    with patch(
        "erieiron_autonomous_agent.workflow.sync_erieiron_application_repo_if_changed"
    ):
        initialize_database_workflows(PubSubManager())

    assert (
        handle_llm_request,
        None,
        None,
    ) in subscribers[PubSubMessageType.LLM_REQUEST]
