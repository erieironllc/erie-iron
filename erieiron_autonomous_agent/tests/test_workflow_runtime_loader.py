import pytest

from erieiron_autonomous_agent.models import (
    WorkflowConnection,
    WorkflowDefinition,
    WorkflowStep,
    WorkflowTrigger,
)
from erieiron_autonomous_agent.workflow import initialize_database_workflows
from erieiron_common.enums import PubSubMessageType
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
