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
        long_term_memory_enabled=True,
        datastore_enabled=True,
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
        message_type=PubSubMessageType.EVERY_MINUTE.value,
        sort_order=0,
    )
    connection = WorkflowConnection.objects.create(
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
    assert context["selected_workflow_data"]["id"] == str(workflow.id)
    assert context["message_type_choices"][0]["value"] == PubSubMessageType.EVERY_MINUTE.value
    external_trigger_message_type_values = [
        choice["value"]
        for choice in context["external_trigger_message_type_choices"]
    ]
    assert external_trigger_message_type_values == [
        PubSubMessageType.EVERY_MINUTE.value,
        PubSubMessageType.EVERY_HOUR.value,
        PubSubMessageType.EVERY_DAY.value,
        PubSubMessageType.EVERY_WEEK.value,
    ]
    assert PubSubMessageType.ANALYSIS_REQUESTED.value not in external_trigger_message_type_values
    assert PubSubMessageType.PORTFOLIO_ADD_BUSINESSES_REQUESTED.value not in external_trigger_message_type_values
    selected_workflow_data = next(
        workflow_data
        for workflow_data in context["workflows_data"]
        if workflow_data["id"] == str(workflow.id)
    )
    assert selected_workflow_data["id"] == str(workflow.id)
    assert selected_workflow_data["name"] == "Admin Workflow"
    assert selected_workflow_data["description"] == "Workflow admin detail"
    assert selected_workflow_data["is_active"] is True
    assert selected_workflow_data["long_term_memory_enabled"] is True
    assert selected_workflow_data["datastore_enabled"] is True
    assert selected_workflow_data["datastore_backend"] == "SQLITE"
    assert selected_workflow_data["datastore_backend_label"] == "SQLite"
    assert selected_workflow_data["steps"] == [
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
    ]
    assert selected_workflow_data["triggers"] == [
        {
            "id": str(workflow.triggers.get().id),
            "workflow_id": str(workflow.id),
            "target_step_id": str(source_step.id),
            "target_step_name": "Analyze request",
            "message_type": PubSubMessageType.EVERY_MINUTE.value,
            "message_type_label": "Every minute",
            "sort_order": 0,
        },
    ]
    assert selected_workflow_data["connections"] == [
        {
            "id": str(connection.id),
            "workflow_id": str(workflow.id),
            "source_step_id": str(source_step.id),
            "source_step_name": "Analyze request",
            "target_step_id": str(target_step.id),
            "target_step_name": "Submit request",
            "message_type": PubSubMessageType.ANALYSIS_REQUESTED.value,
            "message_type_label": "Analysis requested",
            "sort_order": 0,
        },
    ]
    assert selected_workflow_data["step_count"] == 2
    assert selected_workflow_data["trigger_count"] == 1
    assert selected_workflow_data["connection_count"] == 1
    assert selected_workflow_data["diagram"] == {
        "has_content": True,
        "node_count": 3,
        "edge_count": 2,
        "layer_count": 3,
        "layers": [
            {
                "index": 0,
                "kind": "trigger",
                "title": "External Triggers",
                "nodes": [
                    {
                        "id": f"trigger-{workflow.triggers.get().id}",
                        "object_id": str(workflow.triggers.get().id),
                        "kind": "trigger",
                        "title": "Every minute",
                        "subtitle": "Starts Analyze request",
                        "detail": PubSubMessageType.EVERY_MINUTE.value,
                        "sort_order": 0,
                        "column_index": 0,
                        "target_step_id": str(source_step.id),
                        "target_step_name": "Analyze request",
                    },
                ],
            },
            {
                "index": 1,
                "kind": "step",
                "title": "Step Layer 1",
                "nodes": [
                    {
                        "id": f"step-{source_step.id}",
                        "object_id": str(source_step.id),
                        "kind": "step",
                        "title": "Analyze request",
                        "subtitle": "Analysis requested",
                        "detail": "erieiron_autonomous_agent.board_level_agents.board_analyst.on_analysis_requested",
                        "sort_order": 0,
                        "column_index": 0,
                        "handler_path": "erieiron_autonomous_agent.board_level_agents.board_analyst.on_analysis_requested",
                        "emits_message_type": PubSubMessageType.ANALYSIS_REQUESTED.value,
                        "emits_message_type_label": "Analysis requested",
                    },
                ],
            },
            {
                "index": 2,
                "kind": "step",
                "title": "Step Layer 2",
                "nodes": [
                    {
                        "id": f"step-{target_step.id}",
                        "object_id": str(target_step.id),
                        "kind": "step",
                        "title": "Submit request",
                        "subtitle": "No emitted message",
                        "detail": "erieiron_autonomous_agent.board_level_agents.corporate_development_agent.submit_business_opportunity",
                        "sort_order": 1,
                        "column_index": 0,
                        "handler_path": "erieiron_autonomous_agent.board_level_agents.corporate_development_agent.submit_business_opportunity",
                        "emits_message_type": None,
                        "emits_message_type_label": None,
                    },
                ],
            },
        ],
        "edges": [
            {
                "id": f"trigger-edge-{workflow.triggers.get().id}",
                "kind": "trigger",
                "source_node_id": f"trigger-{workflow.triggers.get().id}",
                "target_node_id": f"step-{source_step.id}",
                "label": "Every minute",
                "line_label": PubSubMessageType.EVERY_MINUTE.value,
            },
            {
                "id": f"connection-edge-{connection.id}",
                "kind": "connection",
                "source_node_id": f"step-{source_step.id}",
                "target_node_id": f"step-{target_step.id}",
                "label": "Analysis requested",
                "line_label": PubSubMessageType.ANALYSIS_REQUESTED.value,
            },
        ],
    }


@pytest.mark.django_db
def test_serialize_workflow_diagram_keeps_disconnected_steps_in_first_step_layer():
    workflow = WorkflowDefinition.objects.create(name="Workflow Graph")
    triggered_step = WorkflowStep.objects.create(
        workflow=workflow,
        name="Triggered step",
        handler_path="erieiron_autonomous_agent.board_level_agents.board_analyst.on_analysis_requested",
        emits_message_type=PubSubMessageType.ANALYSIS_REQUESTED.value,
        sort_order=20,
    )
    disconnected_step = WorkflowStep.objects.create(
        workflow=workflow,
        name="Disconnected step",
        handler_path="erieiron_autonomous_agent.board_level_agents.corporate_development_agent.find_new_business_opportunity",
        sort_order=10,
    )
    downstream_step = WorkflowStep.objects.create(
        workflow=workflow,
        name="Downstream step",
        handler_path="erieiron_autonomous_agent.board_level_agents.corporate_development_agent.submit_business_opportunity",
        sort_order=30,
    )
    WorkflowTrigger.objects.create(
        workflow=workflow,
        target_step=triggered_step,
        message_type=PubSubMessageType.PORTFOLIO_ADD_BUSINESSES_REQUESTED.value,
        sort_order=0,
    )
    WorkflowConnection.objects.create(
        workflow=workflow,
        source_step=triggered_step,
        target_step=downstream_step,
        message_type=PubSubMessageType.ANALYSIS_REQUESTED.value,
        sort_order=0,
    )

    diagram = views._serialize_workflow_diagram(workflow)

    assert diagram["layer_count"] == 3
    first_step_layer = next(layer for layer in diagram["layers"] if layer["index"] == 1)
    assert [node["title"] for node in first_step_layer["nodes"]] == [
        "Triggered step",
        "Disconnected step",
    ]
    second_step_layer = next(layer for layer in diagram["layers"] if layer["index"] == 2)
    assert [node["title"] for node in second_step_layer["nodes"]] == ["Downstream step"]


@pytest.mark.django_db
def test_save_workflow_trigger_form_rejects_task_emitted_message_type():
    workflow = WorkflowDefinition.objects.create(name="Workflow Graph")
    triggered_step = WorkflowStep.objects.create(
        workflow=workflow,
        name="Triggered step",
        handler_path="erieiron_autonomous_agent.board_level_agents.board_analyst.on_analysis_requested",
        emits_message_type=PubSubMessageType.ANALYSIS_REQUESTED.value,
        sort_order=0,
    )

    with pytest.raises(
        ValueError,
        match="External triggers currently only support time-based PubSub message types.",
    ):
        views._save_workflow_trigger_form(
            workflow_id=str(workflow.id),
            trigger_id=None,
            target_step_id=str(triggered_step.id),
            message_type=PubSubMessageType.ANALYSIS_REQUESTED.value,
            sort_order=0,
        )


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
def test_save_workflow_definition_form_updates_memory_and_datastore_settings():
    workflow = WorkflowDefinition.objects.create(name="Editable Workflow")

    updated_workflow = views._save_workflow_definition_form(
        workflow_id=str(workflow.id),
        name="Workflow With Memory",
        description="Workflow settings updated",
        is_active=False,
        long_term_memory_enabled=True,
        datastore_enabled=True,
    )

    workflow.refresh_from_db()

    assert updated_workflow.id == workflow.id
    assert workflow.name == "Workflow With Memory"
    assert workflow.description == "Workflow settings updated"
    assert workflow.is_active is False
    assert workflow.long_term_memory_enabled is True
    assert workflow.datastore_enabled is True
    assert workflow.datastore_backend == "SQLITE"


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
