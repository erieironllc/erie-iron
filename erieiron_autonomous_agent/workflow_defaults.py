from django.db import transaction

from erieiron_common.enums import PubSubMessageType, WorkflowDefinitionSourceKind


DEFAULT_WORKFLOW_SPECS = (
    {
        "name": "Board Workflow",
        "description": "Core board-level workflow for business ideation, analysis, and planning.",
        "steps": (
            {
                "name": "Find new business opportunity",
                "handler_path": "erieiron_autonomous_agent.board_level_agents.corporate_development_agent.find_new_business_opportunity",
                "emits_message_type": PubSubMessageType.BUSINESS_IDEA_SUBMITTED.value,
                "sort_order": 10,
            },
            {
                "name": "Submit business opportunity",
                "handler_path": "erieiron_autonomous_agent.board_level_agents.corporate_development_agent.submit_business_opportunity",
                "emits_message_type": PubSubMessageType.ANALYSIS_REQUESTED.value,
                "sort_order": 20,
            },
            {
                "name": "Find niche business ideas",
                "handler_path": "erieiron_autonomous_agent.board_level_agents.corporate_development_agent.find_niche_business_ideas",
                "sort_order": 30,
            },
            {
                "name": "Pick portfolio business",
                "handler_path": "erieiron_autonomous_agent.board_level_agents.corporate_development_agent.on_portfolio_pick_new_business",
                "sort_order": 40,
            },
            {
                "name": "Analyze requested business",
                "handler_path": "erieiron_autonomous_agent.board_level_agents.board_analyst.on_analysis_requested",
                "emits_message_type": PubSubMessageType.ANALYSIS_ADDED.value,
                "sort_order": 50,
            },
            {
                "name": "Perform second opinion evaluation",
                "handler_path": "erieiron_autonomous_agent.board_level_agents.board_analyst.perform_second_opinion_evaluation",
                "sort_order": 60,
            },
            {
                "name": "Define human job descriptions",
                "handler_path": "erieiron_autonomous_agent.board_level_agents.board_analyst.define_human_job_descriptions",
                "sort_order": 70,
            },
            {
                "name": "Plan resources",
                "handler_path": "erieiron_autonomous_agent.board_level_agents.portfolio_resource_planner.on_resource_planning_requested",
                "emits_message_type": PubSubMessageType.ANALYSIS_ADDED.value,
                "sort_order": 80,
            },
            {
                "name": "Reset task test",
                "handler_path": "erieiron_autonomous_agent.coding_agents.coding_agent.on_reset_task_test",
                "sort_order": 90,
            },
        ),
        "triggers": (
            {
                "target_step": "Find new business opportunity",
                "message_type": PubSubMessageType.PORTFOLIO_ADD_BUSINESSES_REQUESTED.value,
                "sort_order": 10,
            },
            {
                "target_step": "Find niche business ideas",
                "message_type": PubSubMessageType.FIND_NICHE_BUSINESS_IDEAS.value,
                "sort_order": 20,
            },
            {
                "target_step": "Pick portfolio business",
                "message_type": PubSubMessageType.PORTFOLIO_PICK_NEW_BUSINESS.value,
                "sort_order": 30,
            },
            {
                "target_step": "Perform second opinion evaluation",
                "message_type": PubSubMessageType.BUSINESS_SECOND_OPINION_EVALUATION_REQUESTED.value,
                "sort_order": 40,
            },
            {
                "target_step": "Define human job descriptions",
                "message_type": PubSubMessageType.BUSINESS_JOB_DESCRIPTIONS_REQUESTED.value,
                "sort_order": 50,
            },
            {
                "target_step": "Plan resources",
                "message_type": PubSubMessageType.RESOURCE_PLANNING_REQUESTED.value,
                "sort_order": 60,
            },
            {
                "target_step": "Reset task test",
                "message_type": PubSubMessageType.RESET_TASK_TEST.value,
                "sort_order": 70,
            },
        ),
        "connections": (
            {
                "source_step": "Find new business opportunity",
                "target_step": "Submit business opportunity",
                "message_type": PubSubMessageType.BUSINESS_IDEA_SUBMITTED.value,
                "sort_order": 10,
            },
            {
                "source_step": "Submit business opportunity",
                "target_step": "Analyze requested business",
                "message_type": PubSubMessageType.ANALYSIS_REQUESTED.value,
                "sort_order": 20,
            },
        ),
    },
    {
        "name": "Business Workflow",
        "description": "Core business-level workflow for guidance, initiative planning, task orchestration, and execution.",
        "steps": (
            {
                "name": "Update business guidance",
                "handler_path": "erieiron_autonomous_agent.business_level_agents.ceo.on_business_guidance_updated",
                "emits_message_type": PubSubMessageType.CEO_DIRECTIVES_ISSUED.value,
                "sort_order": 10,
            },
            {
                "name": "Queue product initiatives",
                "handler_path": "erieiron_common.message_queue.pubsub_manager.PubSubManager.noop",
                "emits_message_type": PubSubMessageType.PRODUCT_INITIATIVES_REQUESTED.value,
                "sort_order": 20,
            },
            {
                "name": "Define initiatives",
                "handler_path": "erieiron_autonomous_agent.business_level_agents.product_lead.define_initiatives",
                "emits_message_type": PubSubMessageType.INITIATIVE_DEFINED.value,
                "sort_order": 30,
            },
            {
                "name": "Define single initiative",
                "handler_path": "erieiron_autonomous_agent.business_level_agents.product_lead.define_single_initiative",
                "emits_message_type": PubSubMessageType.INITIATIVE_DEFINED.value,
                "sort_order": 40,
            },
            {
                "name": "Define initiative tasks",
                "handler_path": "erieiron_autonomous_agent.business_level_agents.eng_lead.define_tasks_for_initiative",
                "emits_message_type": PubSubMessageType.TASK_UPDATED.value,
                "sort_order": 50,
            },
            {
                "name": "Handle product initiatives defined",
                "handler_path": "erieiron_autonomous_agent.business_level_agents.eng_lead.on_product_initiatives_defined",
                "sort_order": 60,
            },
            {
                "name": "Bootstrap business",
                "handler_path": "erieiron_autonomous_agent.business_level_agents.eng_lead.bootstrap_buiness",
                "sort_order": 70,
            },
            {
                "name": "Generate business architecture",
                "handler_path": "erieiron_autonomous_agent.business_level_agents.eng_lead.on_business_architecture_generation_requested",
                "sort_order": 80,
            },
            {
                "name": "Generate initiative architecture",
                "handler_path": "erieiron_autonomous_agent.business_level_agents.eng_lead.on_initiative_architecture_generation_requested",
                "sort_order": 90,
            },
            {
                "name": "Generate initiative tasks on demand",
                "handler_path": "erieiron_autonomous_agent.business_level_agents.eng_lead.define_tasks_for_initiative",
                "sort_order": 100,
            },
            {
                "name": "Generate initiative user documentation",
                "handler_path": "erieiron_autonomous_agent.business_level_agents.eng_lead.on_initiative_user_documentation_generation_requested",
                "sort_order": 110,
            },
            {
                "name": "Handle blocked task",
                "handler_path": "erieiron_autonomous_agent.business_level_agents.eng_lead.on_task_blocked",
                "emits_message_type": PubSubMessageType.TASK_UPDATED.value,
                "sort_order": 120,
            },
            {
                "name": "Update task state",
                "handler_path": "erieiron_autonomous_agent.business_level_agents.task_manager.on_task_updated",
                "sort_order": 130,
            },
            {
                "name": "Complete task",
                "handler_path": "erieiron_autonomous_agent.business_level_agents.task_manager.on_task_complete",
                "sort_order": 140,
            },
            {
                "name": "Fail task",
                "handler_path": "erieiron_autonomous_agent.business_level_agents.task_manager.on_task_failed",
                "sort_order": 150,
            },
            {
                "name": "Record task spend",
                "handler_path": "erieiron_autonomous_agent.business_level_agents.task_manager.on_task_spend",
                "sort_order": 160,
            },
            {
                "name": "Handle initiative green light",
                "handler_path": "erieiron_autonomous_agent.business_level_agents.task_manager.on_initiative_green_lit",
                "sort_order": 170,
            },
            {
                "name": "Do design work",
                "handler_path": "erieiron_autonomous_agent.business_level_agents.worker_design.do_work",
                "emits_message_type": PubSubMessageType.TASK_UPDATED.value,
                "sort_order": 180,
            },
            {
                "name": "Do coding work",
                "handler_path": "erieiron_autonomous_agent.business_level_agents.worker_coder.do_work",
                "emits_message_type": PubSubMessageType.TASK_UPDATED.value,
                "sort_order": 190,
            },
            {
                "name": "Do deployment work",
                "handler_path": "erieiron_autonomous_agent.business_level_agents.worker_coder.do_work",
                "emits_message_type": PubSubMessageType.TASK_UPDATED.value,
                "sort_order": 200,
            },
            {
                "name": "Do human work",
                "handler_path": "erieiron_autonomous_agent.business_level_agents.worker_human.do_work",
                "sort_order": 210,
            },
        ),
        "triggers": (
            {
                "target_step": "Update business guidance",
                "message_type": PubSubMessageType.BOARD_GUIDANCE_UPDATED.value,
                "sort_order": 10,
            },
            {
                "target_step": "Define single initiative",
                "message_type": PubSubMessageType.INITIATIVE_DEFINITION_REQUESTED.value,
                "sort_order": 20,
            },
            {
                "target_step": "Handle product initiatives defined",
                "message_type": PubSubMessageType.PRODUCT_INITIATIVES_DEFINED.value,
                "sort_order": 30,
            },
            {
                "target_step": "Bootstrap business",
                "message_type": PubSubMessageType.BUSINESS_BOOTSTRAP_REQUESTED.value,
                "sort_order": 40,
            },
            {
                "target_step": "Generate business architecture",
                "message_type": PubSubMessageType.BUSINESS_ARCHITECTURE_GENERATION_REQUESTED.value,
                "sort_order": 50,
            },
            {
                "target_step": "Generate initiative architecture",
                "message_type": PubSubMessageType.INITIATIVE_ARCHITECTURE_GENERATION_REQUESTED.value,
                "sort_order": 60,
            },
            {
                "target_step": "Generate initiative tasks on demand",
                "message_type": PubSubMessageType.INITIATIVE_TASKS_GENERATION_REQUESTED.value,
                "sort_order": 70,
            },
            {
                "target_step": "Generate initiative user documentation",
                "message_type": PubSubMessageType.INITIATIVE_USER_DOCUMENTATION_GENERATION_REQUESTED.value,
                "sort_order": 80,
            },
            {
                "target_step": "Handle blocked task",
                "message_type": PubSubMessageType.TASK_BLOCKED.value,
                "sort_order": 90,
            },
            {
                "target_step": "Complete task",
                "message_type": PubSubMessageType.TASK_COMPLETED.value,
                "sort_order": 100,
            },
            {
                "target_step": "Fail task",
                "message_type": PubSubMessageType.TASK_FAILED.value,
                "sort_order": 110,
            },
            {
                "target_step": "Record task spend",
                "message_type": PubSubMessageType.TASK_SPEND.value,
                "sort_order": 120,
            },
            {
                "target_step": "Handle initiative green light",
                "message_type": PubSubMessageType.INITIATIVE_GREEN_LIT.value,
                "sort_order": 130,
            },
            {
                "target_step": "Do design work",
                "message_type": PubSubMessageType.DESIGN_WORK_REQUESTED.value,
                "sort_order": 140,
            },
            {
                "target_step": "Do coding work",
                "message_type": PubSubMessageType.CODING_WORK_REQUESTED.value,
                "sort_order": 150,
            },
            {
                "target_step": "Do deployment work",
                "message_type": PubSubMessageType.DEPLOYMENT_WORK_REQUESTED.value,
                "sort_order": 160,
            },
            {
                "target_step": "Do human work",
                "message_type": PubSubMessageType.HUMAN_WORK_REQUESTED.value,
                "sort_order": 170,
            },
        ),
        "connections": (
            {
                "source_step": "Update business guidance",
                "target_step": "Queue product initiatives",
                "message_type": PubSubMessageType.CEO_DIRECTIVES_ISSUED.value,
                "sort_order": 10,
            },
            {
                "source_step": "Queue product initiatives",
                "target_step": "Define initiatives",
                "message_type": PubSubMessageType.PRODUCT_INITIATIVES_REQUESTED.value,
                "sort_order": 20,
            },
            {
                "source_step": "Define initiatives",
                "target_step": "Define initiative tasks",
                "message_type": PubSubMessageType.INITIATIVE_DEFINED.value,
                "sort_order": 30,
            },
            {
                "source_step": "Define single initiative",
                "target_step": "Define initiative tasks",
                "message_type": PubSubMessageType.INITIATIVE_DEFINED.value,
                "sort_order": 40,
            },
            {
                "source_step": "Define initiative tasks",
                "target_step": "Update task state",
                "message_type": PubSubMessageType.TASK_UPDATED.value,
                "sort_order": 50,
            },
            {
                "source_step": "Handle blocked task",
                "target_step": "Update task state",
                "message_type": PubSubMessageType.TASK_UPDATED.value,
                "sort_order": 60,
            },
            {
                "source_step": "Do design work",
                "target_step": "Update task state",
                "message_type": PubSubMessageType.TASK_UPDATED.value,
                "sort_order": 70,
            },
            {
                "source_step": "Do coding work",
                "target_step": "Update task state",
                "message_type": PubSubMessageType.TASK_UPDATED.value,
                "sort_order": 80,
            },
            {
                "source_step": "Do deployment work",
                "target_step": "Update task state",
                "message_type": PubSubMessageType.TASK_UPDATED.value,
                "sort_order": 90,
            },
        ),
    },
)


INTERNAL_WORKFLOW_SPECS = (
    {
        "name": "Erie Iron Internal Workflow",
        "description": "Runtime-only workflows managed by Erie Iron rather than an application repo.",
        "steps": (
            {
                "name": "Handle LLM request",
                "handler_path": "erieiron_common.llm_request_handler.handle_llm_request",
                "sort_order": 10,
            },
        ),
        "triggers": (
            {
                "target_step": "Handle LLM request",
                "message_type": PubSubMessageType.LLM_REQUEST.value,
                "sort_order": 10,
            },
        ),
        "connections": (),
    },
)


def sync_default_workflows(apps_registry=None):
    return _sync_workflow_specs(
        DEFAULT_WORKFLOW_SPECS,
        WorkflowDefinitionSourceKind.APPLICATION_REPO,
        apps_registry,
    )


def sync_internal_workflows(apps_registry=None):
    workflows = _sync_workflow_specs(
        INTERNAL_WORKFLOW_SPECS,
        WorkflowDefinitionSourceKind.ERIE_IRON_INTERNAL,
        apps_registry,
    )
    _remove_legacy_board_workflow_llm_request_step(apps_registry)
    return workflows


def remove_default_workflows(apps_registry=None):
    WorkflowDefinition, _, _, _ = _resolve_models(apps_registry)
    workflow_names = [workflow_spec["name"] for workflow_spec in DEFAULT_WORKFLOW_SPECS]
    WorkflowDefinition.objects.filter(name__in=workflow_names).delete()


def _sync_workflow_specs(workflow_specs, source_kind, apps_registry=None):
    WorkflowDefinition, WorkflowStep, WorkflowTrigger, WorkflowConnection = _resolve_models(
        apps_registry
    )
    workflows = []

    for workflow_spec in workflow_specs:
        workflows.append(
            _sync_workflow_spec(
                workflow_spec,
                WorkflowDefinition,
                WorkflowStep,
                WorkflowTrigger,
                WorkflowConnection,
                source_kind=source_kind,
            )
        )

    return workflows


def _resolve_models(apps_registry):
    if apps_registry is None:
        from erieiron_autonomous_agent.models import (
            WorkflowConnection,
            WorkflowDefinition,
            WorkflowStep,
            WorkflowTrigger,
        )

        return WorkflowDefinition, WorkflowStep, WorkflowTrigger, WorkflowConnection

    return (
        apps_registry.get_model("erieiron_autonomous_agent", "WorkflowDefinition"),
        apps_registry.get_model("erieiron_autonomous_agent", "WorkflowStep"),
        apps_registry.get_model("erieiron_autonomous_agent", "WorkflowTrigger"),
        apps_registry.get_model("erieiron_autonomous_agent", "WorkflowConnection"),
    )


def _sync_workflow_spec(
    workflow_spec,
    WorkflowDefinition,
    WorkflowStep,
    WorkflowTrigger,
    WorkflowConnection,
    *,
    source_kind,
):
    with transaction.atomic():
        workflow_defaults = {
            "description": workflow_spec.get("description"),
            "is_active": True,
        }
        if any(field.name == "source_kind" for field in WorkflowDefinition._meta.fields):
            workflow_defaults["source_kind"] = WorkflowDefinitionSourceKind(
                source_kind
            ).value

        workflow, _ = WorkflowDefinition.objects.update_or_create(
            name=workflow_spec["name"],
            defaults=workflow_defaults,
        )

        step_ids_by_name = {}
        desired_step_names = []
        for step_spec in workflow_spec["steps"]:
            desired_step_names.append(step_spec["name"])
            step, _ = WorkflowStep.objects.update_or_create(
                workflow=workflow,
                name=step_spec["name"],
                defaults={
                    "handler_path": step_spec["handler_path"],
                    "emits_message_type": step_spec.get("emits_message_type"),
                    "sort_order": step_spec["sort_order"],
                },
            )
            step_ids_by_name[step.name] = step.id

        workflow.triggers.all().delete()
        workflow.connections.all().delete()
        workflow.steps.exclude(name__in=desired_step_names).delete()

        for trigger_spec in workflow_spec["triggers"]:
            WorkflowTrigger.objects.create(
                workflow=workflow,
                target_step_id=step_ids_by_name[trigger_spec["target_step"]],
                message_type=trigger_spec["message_type"],
                sort_order=trigger_spec["sort_order"],
            )

        for connection_spec in workflow_spec["connections"]:
            WorkflowConnection.objects.create(
                workflow=workflow,
                source_step_id=step_ids_by_name[connection_spec["source_step"]],
                target_step_id=step_ids_by_name[connection_spec["target_step"]],
                message_type=connection_spec["message_type"],
                sort_order=connection_spec["sort_order"],
            )

        return workflow


def _remove_legacy_board_workflow_llm_request_step(apps_registry=None):
    WorkflowDefinition, WorkflowStep, _, _ = _resolve_models(apps_registry)
    if not any(field.name == "source_kind" for field in WorkflowDefinition._meta.fields):
        return

    WorkflowStep.objects.filter(
        workflow__name="Board Workflow",
        workflow__source_kind=WorkflowDefinitionSourceKind.APPLICATION_REPO.value,
        name="Handle LLM request",
        handler_path="erieiron_common.llm_request_handler.handle_llm_request",
    ).delete()
