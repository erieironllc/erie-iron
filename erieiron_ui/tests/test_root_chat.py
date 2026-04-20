from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse

from erieiron_autonomous_agent.models import (
    Business,
    BusinessConversation,
    ConversationChange,
    ConversationMessage,
    Initiative,
    Task,
    WorkflowDefinition,
)
from erieiron_autonomous_agent.enums import TaskStatus
from erieiron_common.enums import BusinessIdeaSource, Level, LlmModel, TaskType
from erieiron_common.llm_apis.llm_response import LlmResponse
from erieiron_common.llm_request_handler import handle_llm_request
from erieiron_common.models import Person
from erieiron_ui import views


@pytest.mark.django_db
def test_view_home_builds_root_chat_context():
    erieiron_business = Business.get_erie_iron_business()
    conversation = BusinessConversation.objects.create(
        business=erieiron_business,
        title="Operations Chat",
        status="active",
    )
    assistant_message = ConversationMessage.objects.create(
        conversation=conversation,
        role="assistant",
        content="We can update the workflow after approval.",
    )
    ConversationChange.objects.create(
        conversation=conversation,
        message=assistant_message,
        change_type="workflow",
        change_description="Create a workflow for intake review.",
        change_details={
            "operation": "create",
            "entity_type": "workflow_definition",
            "name": "Intake Review",
            "description": "Handle intake review.",
            "is_active": True,
        },
        approved=False,
        applied=False,
    )

    request = RequestFactory().get("/")
    request.threadlocal_person_cache = SimpleNamespace(is_admin=lambda: True)

    with patch("erieiron_ui.views.send_response", return_value=HttpResponse("ok")) as mock_send_response:
        response = views.view_home(request)

    assert response.status_code == 200
    context = mock_send_response.call_args.args[2]
    assert context["current_conversation"].id == conversation.id
    assert context["current_messages"][0].id == assistant_message.id
    assert context["can_manage_workflows"] is True
    assert context["workflow_admin_url"] == reverse("view_admin_workflows")


@pytest.mark.django_db
def test_root_conversation_create_uses_erie_iron_business(client):
    response = client.post(
        reverse("root_conversation_create"),
        {"title": "Root Ops Chat"},
    )

    assert response.status_code == 200
    payload = response.json()
    conversation = BusinessConversation.objects.get(id=payload["conversation_id"])
    assert conversation.business == Business.get_erie_iron_business()
    assert conversation.initiative is None
    assert conversation.title == "Root Ops Chat"


@pytest.mark.django_db
def test_root_conversation_message_queues_async_request():
    erieiron_business = Business.get_erie_iron_business()
    conversation = BusinessConversation.objects.create(
        business=erieiron_business,
        title="Root Ops Chat",
        status="active",
    )

    request = RequestFactory().post(
        f"/api/root/conversations/{conversation.id}/message/",
        {"message": "Add a new workflow"},
    )

    with patch(
        "erieiron_ui.views.get_current_user",
        return_value=SimpleNamespace(is_admin=lambda: True),
    ), patch(
        "erieiron_ui.views.request_llm_async",
        return_value=SimpleNamespace(id=uuid4()),
    ) as mock_request_llm_async, patch(
        "erieiron_autonomous_agent.business_conversation_manager.RootConversationManager.build_llm_messages",
        return_value=[],
    ) as mock_build_messages:
        response = views.root_conversation_message(request, conversation.id)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "processing"
    assert payload["conversation_id"] == str(conversation.id)
    assert payload["user_message"]["content"] == "Add a new workflow"
    assert mock_build_messages.call_args.kwargs["can_manage_workflows"] is True
    assert mock_request_llm_async.call_args.kwargs["completion_handler_path"] == "erieiron_ui.views.complete_root_conversation_llm_response"
    assert mock_request_llm_async.call_args.kwargs["client_context"]["client_event_type"] == "root_conversation_response"
    conversation.refresh_from_db()
    assert conversation.messages.filter(role="user", content="Add a new workflow").exists()


@pytest.mark.django_db
def test_complete_root_conversation_llm_response_persists_assistant_message():
    erieiron_business = Business.get_erie_iron_business()
    conversation = BusinessConversation.objects.create(
        business=erieiron_business,
        title="Root Ops Chat",
        status="active",
    )
    ConversationMessage.objects.create(
        conversation=conversation,
        role="user",
        content="Add a new workflow",
    )

    llm_request_id = str(uuid4())
    with patch(
        "erieiron_ui.views.RootConversationManager.suggest_conversation_title",
        return_value="Workflow planning",
    ):
        payload = views.complete_root_conversation_llm_response(
            request_payload={},
            llm_response={
                "llm_request_id": llm_request_id,
                "text": """I can prepare that change.
[PROPOSE_CHANGE]
{"change_type":"workflow","change_description":"Create a workflow.","change_details":{"operation":"create","entity_type":"workflow_definition","name":"Workflow","description":"Created from chat","is_active":true}}
[/PROPOSE_CHANGE]""",
            },
            completion_data={"conversation_id": str(conversation.id)},
        )

    conversation.refresh_from_db()
    assistant_message = conversation.messages.get(role="assistant")

    assert payload["conversation_id"] == str(conversation.id)
    assert payload["conversation_title"] == "Workflow planning"
    assert payload["assistant_message"]["id"] == str(assistant_message.id)
    assert len(payload["assistant_message"]["changes"]) == 1
    assert assistant_message.llm_request_id == llm_request_id
    assert conversation.title == "Workflow planning"


@pytest.mark.django_db
def test_handle_llm_request_publishes_root_conversation_payload():
    person = Person.objects.create(
        email="root-chat@example.com",
        name="Root Chat",
    )
    erieiron_business = Business.get_erie_iron_business()
    conversation = BusinessConversation.objects.create(
        business=erieiron_business,
        title="Root Ops Chat",
        status="active",
    )
    ConversationMessage.objects.create(
        conversation=conversation,
        role="user",
        content="Add a new workflow",
    )

    llm_response = LlmResponse(
        text="I can prepare that workflow change.",
        model=LlmModel.OPENAI_GPT_5_1,
        input_token_count=10,
        output_token_count=20,
        chat_millis=123.0,
    )
    llm_response.set_llm_request_id(uuid4())

    with patch(
        "erieiron_common.llm_request_handler.system_agent_llm_interface.llm_chat",
        return_value=llm_response,
    ), patch(
        "erieiron_ui.views.RootConversationManager.suggest_conversation_title",
        return_value=None,
    ), patch(
        "erieiron_common.llm_request_handler.client_message_publisher.publish",
    ) as mock_publish:
        handle_llm_request(
            {
                "person_id": str(person.id),
                "description": "Erie Iron root conversation",
                "messages": [],
                "model": LlmModel.OPENAI_GPT_5_1.value,
                "completion_handler_path": "erieiron_ui.views.complete_root_conversation_llm_response",
                "completion_handler_data": {"conversation_id": str(conversation.id)},
                "client_context": {
                    "client_event_type": "root_conversation_response",
                    "conversation_id": str(conversation.id),
                },
            }
        )

    published_payload = mock_publish.call_args.kwargs["payload"]

    assert published_payload["client_event_type"] == "root_conversation_response"
    assert published_payload["conversation_id"] == str(conversation.id)
    assert published_payload["assistant_message"]["content"] == "I can prepare that workflow change."
    assert conversation.messages.filter(role="assistant", content="I can prepare that workflow change.").exists()


@pytest.mark.django_db
def test_conversation_change_approve_applies_workflow_change_for_admin():
    erieiron_business = Business.get_erie_iron_business()
    conversation = BusinessConversation.objects.create(
        business=erieiron_business,
        title="Workflow Admin Chat",
        status="active",
    )
    assistant_message = ConversationMessage.objects.create(
        conversation=conversation,
        role="assistant",
        content="I can create that workflow.",
    )
    change = ConversationChange.objects.create(
        conversation=conversation,
        message=assistant_message,
        change_type="workflow",
        change_description="Create the intake review workflow.",
        change_details={
            "operation": "create",
            "entity_type": "workflow_definition",
            "name": "Chat Created Workflow",
            "description": "Created from the root chat.",
            "is_active": True,
        },
        approved=False,
        applied=False,
    )

    request = RequestFactory().post(f"/api/conversation/change/{change.id}/approve/")

    with patch(
        "erieiron_ui.views.get_current_user",
        return_value=SimpleNamespace(is_admin=lambda: True),
    ):
        response = views.conversation_change_approve(request, change.id)

    assert response.status_code == 200
    change.refresh_from_db()
    assert change.approved is True
    assert change.applied is True
    assert WorkflowDefinition.objects.filter(name="Chat Created Workflow").exists()
    assert conversation.messages.filter(
        role="assistant",
        content__icontains="workflow change",
    ).exists()


@pytest.mark.django_db
def test_conversation_change_approve_rejects_workflow_change_for_non_admin():
    erieiron_business = Business.get_erie_iron_business()
    conversation = BusinessConversation.objects.create(
        business=erieiron_business,
        title="Workflow Admin Chat",
        status="active",
    )
    assistant_message = ConversationMessage.objects.create(
        conversation=conversation,
        role="assistant",
        content="I can create that workflow.",
    )
    change = ConversationChange.objects.create(
        conversation=conversation,
        message=assistant_message,
        change_type="workflow",
        change_description="Create the intake review workflow.",
        change_details={
            "operation": "create",
            "entity_type": "workflow_definition",
            "name": "Blocked Workflow",
            "description": "Should require admin access.",
            "is_active": True,
        },
        approved=False,
        applied=False,
    )

    request = RequestFactory().post(f"/api/conversation/change/{change.id}/approve/")

    with patch(
        "erieiron_ui.views.get_current_user",
        return_value=SimpleNamespace(is_admin=lambda: False),
    ):
        response = views.conversation_change_approve(request, change.id)

    assert response.status_code == 403
    change.refresh_from_db()
    assert change.approved is False
    assert change.applied is False
    assert not WorkflowDefinition.objects.filter(name="Blocked Workflow").exists()


@pytest.mark.django_db
def test_conversation_change_approve_updates_task_fields():
    task_business = Business.objects.create(
        name="Task Business",
        source=BusinessIdeaSource.HUMAN,
        service_token="task-business",
    )
    initiative = Initiative.objects.create(
        id="initiative-task-update",
        business=task_business,
        title="Task Initiative",
        description="Manage task updates",
        priority=Level.MEDIUM,
    )
    task = Task.objects.create(
        id="task_to_update",
        initiative=initiative,
        task_type=TaskType.HUMAN_WORK,
        status=TaskStatus.NOT_STARTED,
        description="Original task description",
        risk_notes="Original risk notes",
        completion_criteria=["Original criterion"],
    )

    erieiron_business = Business.get_erie_iron_business()
    conversation = BusinessConversation.objects.create(
        business=erieiron_business,
        title="Task Update Chat",
        status="active",
    )
    assistant_message = ConversationMessage.objects.create(
        conversation=conversation,
        role="assistant",
        content="I can update that task.",
    )
    change = ConversationChange.objects.create(
        conversation=conversation,
        message=assistant_message,
        change_type="task",
        change_description="Update the task status and completion criteria.",
        change_details={
            "operation": "update",
            "task_id": task.id,
            "fields": {
                "status": TaskStatus.IN_PROGRESS.value,
                "risk_notes": "Updated risk notes",
                "completion_criteria": ["Updated criterion one", "Updated criterion two"],
            },
        },
        approved=False,
        applied=False,
    )

    request = RequestFactory().post(f"/api/conversation/change/{change.id}/approve/")
    response = views.conversation_change_approve(request, change.id)

    assert response.status_code == 200
    task.refresh_from_db()
    change.refresh_from_db()
    assert change.approved is True
    assert change.applied is True
    assert task.status == TaskStatus.IN_PROGRESS.value
    assert task.risk_notes == "Updated risk notes"
    assert task.completion_criteria == ["Updated criterion one", "Updated criterion two"]
