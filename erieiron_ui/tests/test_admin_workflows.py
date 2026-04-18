from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.test import RequestFactory

from erieiron_autonomous_agent.models import (
    WorkflowConnection,
    WorkflowDefinition,
    WorkflowStep,
    WorkflowTrigger,
)
from erieiron_common.enums import PubSubMessageType
from erieiron_ui import views


@pytest.mark.django_db
def test_view_admin_workflows_builds_selected_workflow_context():
    workflow = WorkflowDefinition.objects.create(
        name="Admin Workflow",
        description="Workflow admin detail",
        is_active=True,
    )
    source_step = WorkflowStep.objects.create(
        workflow=workflow,
        name="Analyze request",
        handler_path="erieiron_autonomous_agent.board_level_agents.board_analyst.on_analysis_requested",
        emits_message_type=PubSubMessageType.ANALYSIS_REQUESTED.value,
        sort_order=0,
    )
    target_step = WorkflowStep.objects.create(
        workflow=workflow,
        name="Submit request",
        handler_path="erieiron_autonomous_agent.board_level_agents.corporate_development_agent.submit_business_opportunity",
        sort_order=1,
    )
    WorkflowTrigger.objects.create(
        workflow=workflow,
        target_step=source_step,
        message_type=PubSubMessageType.PORTFOLIO_ADD_BUSINESSES_REQUESTED.value,
        sort_order=0,
    )
    WorkflowConnection.objects.create(
        workflow=workflow,
        source_step=source_step,
        target_step=target_step,
        message_type=PubSubMessageType.ANALYSIS_REQUESTED.value,
        sort_order=0,
    )

    request = RequestFactory().get(f"/admin/workflows/?workflow_id={workflow.id}")
    erieiron_business = SimpleNamespace(name="Erie Iron")

    with patch("erieiron_ui.views._build_portfolio_tabs", return_value=[{"slug": "admin"}]) as mock_build_tabs, \
            patch("erieiron_ui.views.send_response", return_value=HttpResponse("ok")) as mock_send_response, \
            patch("erieiron_ui.views.Business.get_erie_iron_business", return_value=erieiron_business):
        response = views.view_admin_workflows.__wrapped__(request)

    assert response.status_code == 200
    mock_build_tabs.assert_called_once_with(erieiron_business, request=request)

    context = mock_send_response.call_args.args[2]
    assert context["selected_workflow"].id == workflow.id
    assert context["message_type_choices"][0]["value"] == PubSubMessageType.EVERY_MINUTE.value
    selected_workflow_data = next(
        workflow_data
        for workflow_data in context["workflows_data"]
        if workflow_data["id"] == str(workflow.id)
    )
    assert selected_workflow_data == {
        "id": str(workflow.id),
        "name": "Admin Workflow",
        "description": "Workflow admin detail",
        "is_active": True,
        "steps": [
            {
                "id": str(source_step.id),
                "workflow_id": str(workflow.id),
                "name": "Analyze request",
                "handler_path": "erieiron_autonomous_agent.board_level_agents.board_analyst.on_analysis_requested",
                "emits_message_type": PubSubMessageType.ANALYSIS_REQUESTED.value,
                "emits_message_type_label": "Analysis requested",
                "sort_order": 0,
            },
            {
                "id": str(target_step.id),
                "workflow_id": str(workflow.id),
                "name": "Submit request",
                "handler_path": "erieiron_autonomous_agent.board_level_agents.corporate_development_agent.submit_business_opportunity",
                "emits_message_type": None,
                "emits_message_type_label": None,
                "sort_order": 1,
            },
        ],
        "triggers": [
            {
                "id": str(workflow.triggers.get().id),
                "workflow_id": str(workflow.id),
                "target_step_id": str(source_step.id),
                "target_step_name": "Analyze request",
                "message_type": PubSubMessageType.PORTFOLIO_ADD_BUSINESSES_REQUESTED.value,
                "message_type_label": "Portfolio add businesses requested",
                "sort_order": 0,
            },
        ],
        "connections": [
            {
                "id": str(workflow.connections.get().id),
                "workflow_id": str(workflow.id),
                "source_step_id": str(source_step.id),
                "source_step_name": "Analyze request",
                "target_step_id": str(target_step.id),
                "target_step_name": "Submit request",
                "message_type": PubSubMessageType.ANALYSIS_REQUESTED.value,
                "message_type_label": "Analysis requested",
                "sort_order": 0,
            },
        ],
        "step_count": 2,
        "trigger_count": 1,
        "connection_count": 1,
    }


@pytest.mark.django_db
def test_save_workflow_step_form_updates_name_handler_and_emitted_message():
    workflow = WorkflowDefinition.objects.create(name="Editable Workflow")
    step = WorkflowStep.objects.create(
        workflow=workflow,
        name="Old step",
        handler_path="erieiron_autonomous_agent.board_level_agents.corporate_development_agent.find_new_business_opportunity",
        sort_order=0,
    )

    updated_step = views._save_workflow_step_form(
        workflow_id=str(workflow.id),
        step_id=str(step.id),
        name="New step",
        handler_path="erieiron_autonomous_agent.board_level_agents.corporate_development_agent.submit_business_opportunity",
        emits_message_type=PubSubMessageType.BUSINESS_IDEA_SUBMITTED.value,
        sort_order="5",
    )

    step.refresh_from_db()

    assert updated_step.id == step.id
    assert step.name == "New step"
    assert step.handler_path == "erieiron_autonomous_agent.board_level_agents.corporate_development_agent.submit_business_opportunity"
    assert step.emits_message_type == PubSubMessageType.BUSINESS_IDEA_SUBMITTED.value
    assert step.sort_order == 5


@pytest.mark.django_db
def test_save_workflow_connection_form_rejects_message_type_that_source_does_not_emit():
    workflow = WorkflowDefinition.objects.create(name="Connection Workflow")
    source_step = WorkflowStep.objects.create(
        workflow=workflow,
        name="Source",
        handler_path="erieiron_autonomous_agent.board_level_agents.corporate_development_agent.submit_business_opportunity",
        emits_message_type=PubSubMessageType.ANALYSIS_REQUESTED.value,
    )
    target_step = WorkflowStep.objects.create(
        workflow=workflow,
        name="Target",
        handler_path="erieiron_autonomous_agent.board_level_agents.board_analyst.on_analysis_requested",
    )

    with pytest.raises(ValidationError):
        views._save_workflow_connection_form(
            workflow_id=str(workflow.id),
            connection_id=None,
            source_step_id=str(source_step.id),
            target_step_id=str(target_step.id),
            message_type=PubSubMessageType.BUSINESS_IDEA_SUBMITTED.value,
            sort_order="1",
        )
