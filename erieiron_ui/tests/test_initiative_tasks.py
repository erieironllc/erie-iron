import pytest
from unittest.mock import patch

from django.urls import reverse

from erieiron_autonomous_agent.models import Business, Initiative, Task
from erieiron_common.enums import BusinessIdeaSource, Level, TaskType


class _DummyLlmResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.mark.django_db
def test_action_submit_initiative_task_creates_task(client):
    business = Business.objects.create(
        name="Test Business",
        source=BusinessIdeaSource.HUMAN,
        service_token="svc-token",
    )
    initiative = Initiative.objects.create(
        id="init-123",
        business=business,
        title="Growth Initiative",
        description="Expand product reach",
        priority=Level.MEDIUM,
    )

    llm_payload = {
        "description": "Deliver an onboarding walkthrough for new users.",
        "completion_criteria": [
            "The onboarding walkthrough guides first-time users through the key product areas.",
            "Completion metrics confirm that new users finish the walkthrough.",
        ],
        "risk_notes": "Coordinate with design for visual assets.",
        "task_type": "CODING_APPLICATION",
    }

    with patch(
        "erieiron_autonomous_agent.system_agent_llm_interface.llm_chat",
        return_value=_DummyLlmResponse(llm_payload),
    ) as mock_llm_chat:
        response = client.post(
            reverse("action_submit_initiative_task", args=[initiative.id]),
            {"task_request": "Create an onboarding walkthrough for brand-new users."},
        )

    assert response.status_code == 302
    assert response["Location"] == reverse("view_initiative_tab", args=["tasks", initiative.id])
    mock_llm_chat.assert_called_once()

    task = Task.objects.get(initiative=initiative)
    assert task.description == llm_payload["description"]
    assert task.completion_criteria == llm_payload["completion_criteria"]
    assert task.risk_notes == llm_payload["risk_notes"]
    assert task.task_type == TaskType.CODING_APPLICATION


@pytest.mark.django_db
def test_action_submit_initiative_task_rejects_empty_request(client):
    business = Business.objects.create(
        name="Empty Task Business",
        source=BusinessIdeaSource.HUMAN,
        service_token="svc-empty",
    )
    initiative = Initiative.objects.create(
        id="init-empty",
        business=business,
        title="Retention Initiative",
        description="Improve retention",
        priority=Level.LOW,
    )

    with patch("erieiron_autonomous_agent.system_agent_llm_interface.llm_chat") as mock_llm_chat:
        response = client.post(
            reverse("action_submit_initiative_task", args=[initiative.id]),
            {"task_request": ""},
        )

    assert response.status_code == 302
    assert response["Location"] == reverse("view_initiative_tab", args=["tasks", initiative.id])
    assert Task.objects.filter(initiative=initiative).count() == 0
    mock_llm_chat.assert_not_called()


@pytest.mark.django_db
def test_action_submit_initiative_task_defaults_invalid_task_type(client):
    business = Business.objects.create(
        name="Fallback Task Business",
        source=BusinessIdeaSource.HUMAN,
        service_token="svc-fallback",
    )
    initiative = Initiative.objects.create(
        id="init-fallback",
        business=business,
        title="Automation Initiative",
        description="Automate operations",
        priority=Level.MEDIUM,
    )

    llm_payload = {
        "description": "Add weekly data export automation.",
        "completion_criteria": [
            "The system exports a CSV every week with the latest metrics.",
            "The export is delivered to the analytics inbox and archived in S3.",
        ],
        "risk_notes": "",
        "task_type": "SOMETHING_NEW",
    }

    with patch(
        "erieiron_autonomous_agent.system_agent_llm_interface.llm_chat",
        return_value=_DummyLlmResponse(llm_payload),
    ):
        response = client.post(
            reverse("action_submit_initiative_task", args=[initiative.id]),
            {"task_request": "Automate a weekly export of our analytics metrics."},
        )

    assert response.status_code == 302
    assert response["Location"] == reverse("view_initiative_tab", args=["tasks", initiative.id])

    task = Task.objects.get(initiative=initiative)
    assert task.task_type == TaskType.HUMAN_WORK
