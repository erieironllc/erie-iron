from django.db import transaction

from erieiron_autonomous_agent.system_agent_llm_interface import business_level_chat
from erieiron_common import common
from erieiron_common.enums import TaskStatus, TaskExecutionMode, TaskAssigneeType, TaskPhase, TaskExecutionType, TaskExecutionSchedule, InitiativeType
from erieiron_common.models import (
    Initiative,
    Task,
    ProductRequirement,
    TaskDesignRequirements,
    DesignComponent
)


def on_task_blocked(payload):
    task = Task.objects.get(id=payload['task_id'])
    initiative = task.initiative
    business = initiative.business

    payload["GOAL"] = "unblock this task"

    # --- Add existing tasks
    payload["existing_tasks"] = [
        {
            "task_id": t.id,
            "description": t.description,
            "status": t.status,
            "phase": t.phase,
            "type": t.task_type,
        }
        for t in initiative.tasks.all()
    ]

    # --- Add outputs from executed tasks (if tracked)
    payload["executed_tasks"] = [
        {
            "task_id": t.id,
            "output": t.get_last_execution().output,
            "status": t.status,
        }
        for t in initiative.tasks.filter(status=TaskStatus.COMPLETE)
        if hasattr(t, "execution_output")
    ]

    eng_lead_response = business_level_chat(
        ["eng_lead--unblocker.md", "eng_lead.md"],
        payload,
        output_schema="eng_lead.md.schema.json",
        replacements=[
            ("<iam_role_name>", business.get_iam_role_name())
        ]
    )

    new_task_ids = process_response(initiative, eng_lead_response)
    with transaction.atomic():
        Task.objects.filter(id=task.id).update(
            status=TaskStatus.BLOCKED
        )

        # Preserve existing dependencies and append new blocking tasks, no duplication or self-reference
        existing_dep_ids = set(task.depends_on.values_list("id", flat=True))
        new_blocking_ids = set(new_task_ids) - {task.id}
        combined_dep_ids = existing_dep_ids.union(new_blocking_ids)
        task.depends_on.set(Task.objects.filter(id__in=combined_dep_ids))

    return new_task_ids


def define_tasks_for_initiative(initiative_id):
    initiative = Initiative.objects.get(id=initiative_id)
    business = initiative.business

    chat_data = build_chat_data(business, initiative)

    eng_lead_response = business_level_chat(
        "eng_lead.md",
        chat_data,
        replacements=[
            ("<iam_role_name>", business.get_iam_role_name())
        ]
    )

    return process_response(initiative, eng_lead_response)


def build_chat_data(business, initiative):
    requirements = [
        {
            "id": req.id,
            "summary": req.summary,
            "acceptance_criteria": req.acceptance_criteria,
        } for req in initiative.requirements.all()
    ]

    if InitiativeType.ENGINEERING.eq(initiative.initiative_type):
        linked_goals = linked_kpis = ["Engineeering Initiative - not linked to Goals / KPIs"]
    else:
        linked_kpis = [
            {
                "id": kpi.id,
                "name": kpi.name
            } for kpi in initiative.linked_kpis.all()
        ]

        linked_goals = [
            {
                "id": goal.id,
                "description": goal.description
            } for goal in initiative.linked_goals.all()
        ]

    existing_tasks = [
        {
            "task_id": task.id,
            "task_description": task.description
        }
        for task in initiative.tasks.all()
    ]

    return {
        "business_name": business.name,
        "initiative_id": initiative.id,
        "initiative_title": initiative.title,
        "initiative_description": initiative.description,
        "requirements": requirements,
        "linked_kpis": linked_kpis,
        "linked_goals": linked_goals,
        "existing_tasks": existing_tasks,
    }


@transaction.atomic
def process_response(initiative, eng_lead_response):
    updated_or_created_task_ids = []

    for task_data in eng_lead_response.get("tasks", []):
        task_id = task_data.get("task_id")

        base_required_fields = [
            "task_id",
            "depends_on",
            "task_description",
            "risk_notes",
            "test_plan",
            "role_assignee",
            "completion_criteria",
        ]

        for field in base_required_fields:
            if field not in task_data:
                raise ValueError(f"Missing required task field '{field}': {task_data}")

        if "completion_criteria" not in task_data or not isinstance(task_data["completion_criteria"], list) or not task_data["completion_criteria"]:
            raise ValueError(f"'completion_criteria' is required and must be a non-empty list: {task_data}")

        validated_ids = set(task_data.get("validated_requirements", []))
        initiative_req_ids = set(str(req.id) for req in initiative.requirements.all())
        invalid_ids = validated_ids - initiative_req_ids

        if invalid_ids:
            raise ValueError(f"Task {task_data.get('task_id')} references invalid requirements: {invalid_ids}")

        phase = task_data.get("phase")
        TaskPhase.valid(phase)
        if not TaskPhase.valid(phase):
            raise ValueError(f"Invalid or missing phase for task {task_id}: {phase}")

        task_type = task_data.get("task_type")
        if TaskPhase.EXECUTE.eq(phase):
            if not TaskExecutionType.valid(task_type):
                raise ValueError(f"Invalid task_type for task {task_id}: {task_type}")

        eng_task, created = Task.objects.update_or_create(
            id=task_id if task_id else None,
            defaults={
                "initiative": initiative,
                "status": TaskStatus.NOT_STARTED,
                "description": task_data.get("task_description", ""),
                "risk_notes": task_data.get("risk_notes", ""),
                "test_plan": task_data.get("test_plan", ""),
                "role_assignee": TaskAssigneeType(task_data.get("role_assignee", TaskAssigneeType.ENGINEERING)),
                "completion_criteria": task_data.get("completion_criteria"),
                "phase": TaskPhase(phase),
                "task_type": TaskExecutionType.valid_or(task_type),
                "execution_mode": TaskExecutionMode(task_data.get("execution_mode", TaskExecutionMode.CONTAINER)),
                "requires_test": initiative.requires_unit_tests and common.parse_bool(task_data.get("requires_test", True)),
                "execution_schedule": TaskExecutionSchedule(task_data.get("execution_schedule", TaskExecutionSchedule.ONCE)),
                "execution_start_time": task_data.get("execution_start_time", None),
            }
        )

        # Handle design_handoff for DESIGN tasks
        if TaskAssigneeType.DESIGN.eq(eng_task.role_assignee) and "design_handoff" in task_data:
            handoff_data = task_data["design_handoff"]
            handoff_obj, _ = TaskDesignRequirements.objects.get_or_create(task=eng_task)

            # Components
            component_ids = handoff_data.get("component_ids", [])
            components = []
            for comp_id in component_ids:
                component, _ = DesignComponent.objects.get_or_create(id=comp_id, defaults={"name": comp_id})
                components.append(component)
            handoff_obj.component_ids.set(components)

            # Layout
            layout = handoff_data.get("layout")
            if isinstance(layout, dict):
                handoff_obj.layout = layout
            elif layout is not None:
                raise ValueError(f"Invalid layout structure in design_handoff for task {eng_task.id}")

            handoff_obj.save()

        # Set depends_on M2M relationship after update_or_create
        dependency_ids = task_data.get("depends_on", [])
        dependencies = Task.objects.filter(id__in=dependency_ids)

        # Dependency validation before assignment
        resolved_ids = set(dependencies.values_list("id", flat=True))
        missing = set(dependency_ids) - resolved_ids
        if missing:
            raise ValueError(f"Task {task_id} has unresolved dependencies: {missing}")

        eng_task.depends_on.set(dependencies)
        eng_task.validated_requirements.set(
            list(ProductRequirement.objects.filter(id__in=task_data.get("validated_requirements", [])))
        )

        fields_to_compare = {
            "description": task_data.get("task_description", ""),
            "risk_notes": task_data.get("risk_notes", ""),
            "test_plan": task_data.get("test_plan", ""),
            "role_assignee": task_data.get("role_assignee"),
            "completion_criteria": task_data.get("completion_criteria"),
        }

        was_updated = any(
            getattr(eng_task, field) != value for field, value in fields_to_compare.items()
        )

        # Compare current depends_on to input for update detection
        depends_on_ids = set(eng_task.depends_on.values_list("id", flat=True))
        input_dep_ids = set(dependency_ids)
        if depends_on_ids != input_dep_ids:
            was_updated = True

        current_req_ids = set(eng_task.validated_requirements.values_list("id", flat=True))
        input_req_ids = set(task_data.get("validated_requirements", []))
        if current_req_ids != input_req_ids:
            was_updated = True

        if created or was_updated:
            updated_or_created_task_ids.append(eng_task.id)

    return updated_or_created_task_ids
