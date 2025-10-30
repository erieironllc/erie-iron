import json

import pytest
from django.urls import reverse

from erieiron_autonomous_agent.enums import TaskStatus, TaskType
from erieiron_autonomous_agent.models import Business, Initiative, InfrastructureStack, Task
from erieiron_common.enums import EnvironmentType, BusinessIdeaSource, InfrastructureStackType, Level


@pytest.mark.django_db
def test_infrastructure_stacks_tab_lists_stacks(client):
    business = Business.objects.create(
        name="Test Biz",
        source=BusinessIdeaSource.HUMAN.value,
        service_token="testbiz",
    )

    initiative = Initiative.objects.create(
        id="init-123",
        business=business,
        title="Demo Initiative",
        description="Demo description",
        priority=Level.MEDIUM.value,
    )

    Task.objects.create(
        id="task-1",
        initiative=initiative,
        task_type=TaskType.CODING_APPLICATION.value,
        status=TaskStatus.NOT_STARTED.value,
        description="Implement feature",
        risk_notes="",
        completion_criteria=[],
        comment_requests=[],
        attachments=[],
        input_fields={},
        output_fields=[],
    )

    tofu_metadata = {
        "provider": "opentofu",
        "workspace_name": "demo-app",
        "workspace_dir": "/workspaces/demo-app",
        "state_file": "/workspaces/demo-app/terraform.tfstate",
        "state_locator": "opentofu://workspace/demo-app",
    }

    stack_with_opentofu = InfrastructureStack.objects.create(
        business=business,
        initiative=initiative,
        stack_namespace_token="stk1a",
        stack_name="stk1a-init-application",
        stack_arn=json.dumps(tofu_metadata, sort_keys=True),
        env_type=EnvironmentType.DEV.value,
        stack_type=InfrastructureStackType.APPLICATION.value,
    )
    stack_with_cf = InfrastructureStack.objects.create(
        business=business,
        initiative=initiative,
        stack_namespace_token="stk1f",
        stack_name="stk1f-init-foundation",
        stack_arn="arn:aws:cloudformation:us-west-2:123456789012:stack/stk1f-init-foundation/abc",
        env_type=EnvironmentType.DEV.value,
        stack_type=InfrastructureStackType.FOUNDATION.value,
    )

    response = client.get(
        reverse("view_initiative_tab", args=["infrastructure-stacks", initiative.id])
    )

    assert response.status_code == 200
    stack_entries = {entry["stack_namespace_token"]: entry for entry in response.context["stack_entries"]}

    assert set(stack_entries) == {stack_with_opentofu.stack_namespace_token, stack_with_cf.stack_namespace_token}

    entry_with_tofu = stack_entries[stack_with_opentofu.stack_namespace_token]
    assert entry_with_tofu["iac_provider"] == "opentofu"
    assert entry_with_tofu["iac_console_url"] is None
    assert entry_with_tofu["iac_state_locator"] == tofu_metadata["state_locator"]
    assert stack_with_opentofu.stack_namespace_token in entry_with_tofu["cloudwatch_logs_url"]

    entry_with_cf = stack_entries[stack_with_cf.stack_namespace_token]
    assert entry_with_cf["iac_provider"] == "cloudformation"
    assert entry_with_cf["iac_state_locator"] == stack_with_cf.stack_arn
    assert entry_with_cf["iac_console_url"].startswith(
        "https://console.aws.amazon.com/cloudformation/home#/stacks/stackinfo?stackId="
    )
    assert stack_with_cf.stack_namespace_token in entry_with_cf["cloudwatch_logs_url"]

    assert response.context["child_task_count"] == 1
