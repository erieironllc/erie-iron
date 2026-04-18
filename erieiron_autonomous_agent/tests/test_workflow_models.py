import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from erieiron_autonomous_agent.models import (
    WorkflowConnection,
    WorkflowDefinition,
    WorkflowStep,
    WorkflowTrigger,
)
from erieiron_common.enums import PubSubMessageType


@pytest.mark.django_db
def test_workflow_models_persist_triggers_steps_and_connections():
    workflow = WorkflowDefinition.objects.create(name="Custom Board Workflow")
    find_ideas = WorkflowStep.objects.create(
        workflow=workflow,
        name="Find ideas",
        handler_path="erieiron_autonomous_agent.board_level_agents.corporate_development_agent.find_new_business_opportunity",
        emits_message_type=PubSubMessageType.BUSINESS_IDEA_SUBMITTED.value,
        sort_order=0,
    )
    submit_idea = WorkflowStep.objects.create(
        workflow=workflow,
        name="Submit idea",
        handler_path="erieiron_autonomous_agent.board_level_agents.corporate_development_agent.submit_business_opportunity",
        emits_message_type=PubSubMessageType.ANALYSIS_REQUESTED.value,
        sort_order=1,
    )

    trigger = WorkflowTrigger.objects.create(
        workflow=workflow,
        target_step=find_ideas,
        message_type=PubSubMessageType.PORTFOLIO_ADD_BUSINESSES_REQUESTED.value,
        sort_order=0,
    )
    connection = WorkflowConnection.objects.create(
        workflow=workflow,
        source_step=find_ideas,
        target_step=submit_idea,
        message_type=PubSubMessageType.BUSINESS_IDEA_SUBMITTED.value,
        sort_order=0,
    )

    assert list(workflow.steps.values_list("name", flat=True)) == [
        "Find ideas",
        "Submit idea",
    ]
    assert trigger.target_step_id == find_ideas.id
    assert connection.source_step_id == find_ideas.id
    assert connection.target_step_id == submit_idea.id


@pytest.mark.django_db
def test_workflow_trigger_requires_target_step_in_same_workflow():
    board_workflow = WorkflowDefinition.objects.create(name="Custom Board Workflow")
    business_workflow = WorkflowDefinition.objects.create(name="Custom Business Workflow")
    business_step = WorkflowStep.objects.create(
        workflow=business_workflow,
        name="Define tasks",
        handler_path="erieiron_autonomous_agent.business_level_agents.eng_lead.define_tasks_for_initiative",
    )

    trigger = WorkflowTrigger(
        workflow=board_workflow,
        target_step=business_step,
        message_type=PubSubMessageType.INITIATIVE_DEFINED.value,
    )

    with pytest.raises(ValidationError):
        trigger.full_clean()


@pytest.mark.django_db
def test_workflow_connection_requires_same_workflow_and_matching_message_type():
    board_workflow = WorkflowDefinition.objects.create(name="Custom Board Workflow")
    business_workflow = WorkflowDefinition.objects.create(name="Custom Business Workflow")
    source_step = WorkflowStep.objects.create(
        workflow=board_workflow,
        name="Submit idea",
        handler_path="erieiron_autonomous_agent.board_level_agents.corporate_development_agent.submit_business_opportunity",
        emits_message_type=PubSubMessageType.ANALYSIS_REQUESTED.value,
    )
    board_target = WorkflowStep.objects.create(
        workflow=board_workflow,
        name="Analyze business",
        handler_path="erieiron_autonomous_agent.board_level_agents.board_analyst.on_analysis_requested",
    )
    business_target = WorkflowStep.objects.create(
        workflow=business_workflow,
        name="Define tasks",
        handler_path="erieiron_autonomous_agent.business_level_agents.eng_lead.define_tasks_for_initiative",
    )

    cross_workflow_connection = WorkflowConnection(
        workflow=board_workflow,
        source_step=source_step,
        target_step=business_target,
        message_type=PubSubMessageType.ANALYSIS_REQUESTED.value,
    )
    mismatched_message_connection = WorkflowConnection(
        workflow=board_workflow,
        source_step=source_step,
        target_step=board_target,
        message_type=PubSubMessageType.BUSINESS_IDEA_SUBMITTED.value,
    )

    with pytest.raises(ValidationError):
        cross_workflow_connection.full_clean()
    with pytest.raises(ValidationError):
        mismatched_message_connection.full_clean()


@pytest.mark.django_db
def test_workflow_models_prevent_duplicate_step_and_connection_definitions():
    workflow = WorkflowDefinition.objects.create(name="Custom Board Workflow")
    find_ideas = WorkflowStep.objects.create(
        workflow=workflow,
        name="Find ideas",
        handler_path="erieiron_autonomous_agent.board_level_agents.corporate_development_agent.find_new_business_opportunity",
        emits_message_type=PubSubMessageType.BUSINESS_IDEA_SUBMITTED.value,
    )
    submit_idea = WorkflowStep.objects.create(
        workflow=workflow,
        name="Submit idea",
        handler_path="erieiron_autonomous_agent.board_level_agents.corporate_development_agent.submit_business_opportunity",
    )

    WorkflowConnection.objects.create(
        workflow=workflow,
        source_step=find_ideas,
        target_step=submit_idea,
        message_type=PubSubMessageType.BUSINESS_IDEA_SUBMITTED.value,
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        WorkflowStep.objects.create(
            workflow=workflow,
            name="Find ideas",
            handler_path="erieiron_autonomous_agent.board_level_agents.corporate_development_agent.find_niche_business_ideas",
        )

    with pytest.raises(IntegrityError), transaction.atomic():
        WorkflowConnection.objects.create(
            workflow=workflow,
            source_step=find_ideas,
            target_step=submit_idea,
            message_type=PubSubMessageType.BUSINESS_IDEA_SUBMITTED.value,
        )


@pytest.mark.django_db
def test_workflow_models_allow_shared_handler_paths_for_distinct_steps():
    workflow = WorkflowDefinition.objects.create(name="Custom Business Workflow")

    first_step = WorkflowStep.objects.create(
        workflow=workflow,
        name="Define initiative tasks",
        handler_path="erieiron_autonomous_agent.business_level_agents.eng_lead.define_tasks_for_initiative",
        emits_message_type=PubSubMessageType.TASK_UPDATED.value,
    )
    second_step = WorkflowStep.objects.create(
        workflow=workflow,
        name="Generate initiative tasks on demand",
        handler_path="erieiron_autonomous_agent.business_level_agents.eng_lead.define_tasks_for_initiative",
    )

    assert first_step.handler_path == second_step.handler_path
    assert workflow.steps.count() == 2
