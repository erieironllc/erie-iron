import json

import pytest
from django.urls import reverse

from erieiron_autonomous_agent.models import Business, Initiative, InfrastructureStack, Task
from erieiron_autonomous_agent.enums import TaskStatus, TaskType
from erieiron_common.enums import AwsEnv, BusinessIdeaSource, InfrastructureStackType, Level, InitiativeType


@pytest.mark.django_db
def test_business_infrastructure_stacks_tab_lists_all_stacks(client):
    business = Business.objects.create(
        name="Biz",
        source=BusinessIdeaSource.HUMAN.value,
        service_token="biztoken",
    )

    initiative = Initiative.objects.create(
        id="init-456",
        business=business,
        title="Customer Portal",
        description="Portal build",
        priority=Level.HIGH.value,
    )

    initiative_stack_metadata = {
        "provider": "opentofu",
        "workspace_name": "app-dev",
        "workspace_dir": "/workspaces/app-dev",
        "state_file": "/workspaces/app-dev/terraform.tfstate",
        "state_locator": "opentofu://workspace/app-dev",
    }

    initiative_stack = InfrastructureStack.objects.create(
        business=business,
        initiative=initiative,
        stack_namespace_token="stk-aa",
        stack_name="stk-aa-customer-portal",
        stack_arn=json.dumps(initiative_stack_metadata, sort_keys=True),
        aws_env=AwsEnv.DEV.value,
        stack_type=InfrastructureStackType.APPLICATION.value,
    )

    business_stack = InfrastructureStack.objects.create(
        business=business,
        stack_namespace_token="stk-bb",
        stack_name="stk-bb-shared",
        stack_arn="arn:aws:cloudformation:us-west-2:123456789012:stack/stk-bb-shared/1",
        aws_env=AwsEnv.PRODUCTION.value,
        stack_type=InfrastructureStackType.FOUNDATION.value,
    )

    response = client.get(
        reverse("view_business_tab", args=["infrastructure-stacks", business.id])
    )

    assert response.status_code == 200

    stack_entries_list = response.context["stack_entries"]
    assert [entry["scope_label"] for entry in stack_entries_list] == [
        "Business",
        initiative.title,
    ]

    stack_entries = {entry["stack_namespace_token"]: entry for entry in stack_entries_list}
    assert set(stack_entries) == {initiative_stack.stack_namespace_token, business_stack.stack_namespace_token}

    initiative_entry = stack_entries[initiative_stack.stack_namespace_token]
    assert initiative_entry["scope_label"] == initiative.title
    assert initiative_entry["initiative_id"] == initiative.id
    assert initiative_entry["initiative_title"] == initiative.title
    assert initiative_entry["iac_provider"] == "opentofu"
    assert initiative_entry["iac_console_url"] is None
    assert initiative_entry["iac_state_locator"] == initiative_stack_metadata["state_locator"]
    assert initiative_entry["iac_state_metadata"]["workspace_dir"] == initiative_stack_metadata["workspace_dir"]

    business_entry = stack_entries[business_stack.stack_namespace_token]
    assert business_entry["scope_label"] == "Business"
    assert business_entry["initiative_title"] is None
    assert business_entry["iac_provider"] == "cloudformation"
    assert business_entry["iac_state_locator"] == business_stack.stack_arn
    assert "stackinfo?stackId=" in business_entry["iac_console_url"]

    assert initiative_entry["stack_namespace_token"] in initiative_entry["cloudwatch_logs_url"]
    assert business_entry["stack_namespace_token"] in business_entry["cloudwatch_logs_url"]

    assert response.context["stack_count"] == 2
    assert response.context["initiative_stack_count"] == 1
    assert response.context["business_scoped_stack_count"] == 1


@pytest.mark.django_db
def test_production_push_creates_operational_task(client):
    business = Business.objects.create(
        name="Biz",
        source=BusinessIdeaSource.HUMAN.value,
        service_token="biztoken",
    )

    response = client.post(
        reverse("action_business_production_push", args=[business.id]),
        follow=False,
    )

    assert response.status_code == 302

    initiative = Initiative.objects.get(business=business, title="Operational Tasks")
    task = Task.objects.get(initiative=initiative)

    assert task.task_type == TaskType.PRODUCTION_DEPLOYMENT
    assert task.status == TaskStatus.NOT_STARTED
    assert "Production push requested" in task.description
    assert task.requires_test is False


@pytest.mark.django_db
def test_production_push_reuses_operational_initiative(client):
    business = Business.objects.create(
        name="Biz",
        source=BusinessIdeaSource.HUMAN.value,
        service_token="biztoken",
    )

    initiative = Initiative.objects.create(
        id="ops-initiative",
        business=business,
        title="Operational Tasks",
        description="Ops backlog",
        priority=Level.MEDIUM.value,
        initiative_type=InitiativeType.ENGINEERING,
        requires_unit_tests=False,
    )

    response = client.post(
        reverse("action_business_production_push", args=[business.id]),
        follow=False,
    )

    assert response.status_code == 302

    initiative.refresh_from_db()

    tasks = list(Task.objects.filter(initiative=initiative))
    assert len(tasks) == 1
    assert tasks[0].task_type == TaskType.PRODUCTION_DEPLOYMENT
    assert Initiative.objects.filter(business=business, title="Operational Tasks").count() == 1
