import pytest
from django.urls import reverse

from erieiron_autonomous_agent.models import Business, Initiative, InfrastructureStack
from erieiron_common.enums import AwsEnv, BusinessIdeaSource, InfrastructureStackType, Level


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

    initiative_stack = InfrastructureStack.objects.create(
        business=business,
        initiative=initiative,
        stack_namespace_token="stk-aa",
        stack_name="stk-aa-customer-portal",
        stack_arn="arn:aws:cloudformation:us-west-2:123456789012:stack/stk-aa-customer-portal/4d5e6f",
        aws_env=AwsEnv.DEV.value,
        stack_type=InfrastructureStackType.APPLICATION.value,
    )

    business_stack = InfrastructureStack.objects.create(
        business=business,
        stack_namespace_token="stk-bb",
        stack_name="stk-bb-shared",
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
    assert initiative_entry["cloudformation_url"].startswith(
        "https://console.aws.amazon.com/cloudformation/home#/stacks/stackinfo?stackId="
    )

    business_entry = stack_entries[business_stack.stack_namespace_token]
    assert business_entry["scope_label"] == "Business"
    assert business_entry["initiative_title"] is None
    assert "filteringStatus=active" in business_entry["cloudformation_url"]

    assert initiative_entry["stack_namespace_token"] in initiative_entry["cloudwatch_logs_url"]
    assert business_entry["stack_namespace_token"] in business_entry["cloudwatch_logs_url"]

    assert response.context["stack_count"] == 2
    assert response.context["initiative_stack_count"] == 1
    assert response.context["business_scoped_stack_count"] == 1
