import pytest

from erieiron_autonomous_agent.models import WorkflowDefinition
from erieiron_autonomous_agent.workflow_defaults import sync_default_workflows
from erieiron_common.enums import PubSubMessageType


@pytest.mark.django_db
def test_sync_default_workflows_creates_board_and_business_graph():
    workflows = sync_default_workflows()

    assert [workflow.name for workflow in workflows] == [
        "Board Workflow",
        "Business Workflow",
    ]

    board_workflow = WorkflowDefinition.objects.get(name="Board Workflow")
    assert board_workflow.long_term_memory_enabled is False
    assert board_workflow.datastore_enabled is False
    assert board_workflow.datastore_backend == "SQLITE"
    assert board_workflow.steps.filter(
        name="Find new business opportunity",
        emits_message_type=PubSubMessageType.BUSINESS_IDEA_SUBMITTED.value,
    ).exists()
    assert board_workflow.triggers.filter(
        target_step__name="Find new business opportunity",
        message_type=PubSubMessageType.PORTFOLIO_ADD_BUSINESSES_REQUESTED.value,
    ).exists()
    assert board_workflow.connections.filter(
        source_step__name="Find new business opportunity",
        target_step__name="Submit business opportunity",
        message_type=PubSubMessageType.BUSINESS_IDEA_SUBMITTED.value,
    ).exists()

    business_workflow = WorkflowDefinition.objects.get(name="Business Workflow")
    assert business_workflow.long_term_memory_enabled is False
    assert business_workflow.datastore_enabled is False
    assert business_workflow.datastore_backend == "SQLITE"
    assert business_workflow.steps.filter(
        name="Queue product initiatives",
        handler_path="erieiron_common.message_queue.pubsub_manager.PubSubManager.noop",
        emits_message_type=PubSubMessageType.PRODUCT_INITIATIVES_REQUESTED.value,
    ).exists()
    assert business_workflow.connections.filter(
        source_step__name="Queue product initiatives",
        target_step__name="Define initiatives",
        message_type=PubSubMessageType.PRODUCT_INITIATIVES_REQUESTED.value,
    ).exists()
    assert business_workflow.connections.filter(
        source_step__name="Do coding work",
        target_step__name="Update task state",
        message_type=PubSubMessageType.TASK_UPDATED.value,
    ).exists()


@pytest.mark.django_db
def test_sync_default_workflows_is_idempotent():
    sync_default_workflows()
    sync_default_workflows()

    board_workflow = WorkflowDefinition.objects.get(name="Board Workflow")
    business_workflow = WorkflowDefinition.objects.get(name="Business Workflow")

    assert WorkflowDefinition.objects.count() == 2
    assert board_workflow.steps.count() == 10
    assert board_workflow.triggers.count() == 8
    assert board_workflow.connections.count() == 2
    assert business_workflow.steps.count() == 21
    assert business_workflow.triggers.count() == 17
    assert business_workflow.connections.count() == 9
