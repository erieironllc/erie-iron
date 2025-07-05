from django.db import transaction

from erieiron_autonomous_agent.enums import TaskStatus
from erieiron_autonomous_agent.models import (
    Initiative,
    Task,
    ProductRequirement
)
from erieiron_autonomous_agent.system_agent_llm_interface import business_level_chat
from erieiron_common import common
from erieiron_common.enums import TaskExecutionSchedule, InitiativeType, TaskType


def on_task_blocked(payload, msg):
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

    new_task_ids = process_response(msg, initiative, eng_lead_response)
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


def define_tasks_for_initiative(initiative_id, msg):
    initiative = Initiative.objects.get(id=initiative_id)
    business = initiative.business

    chat_data = build_chat_data(business, initiative)

    eng_lead_response = business_level_chat(
        "eng_lead.md",
        chat_data,
        output_schema="eng_lead.md.schema.json",
        replacements=[
            ("<iam_role_name>", business.get_iam_role_name())
        ]
    )

    return process_response(msg, initiative, eng_lead_response)


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
def process_response(msg, initiative, eng_lead_response):
    updated_or_created_task_ids = []

    for task_data in eng_lead_response.get("tasks", []):
        task_id = task_data.get("task_id")

        validated_ids = set(task_data.get("validated_requirements", []))
        initiative_req_ids = set(str(req.id) for req in initiative.requirements.all())
        invalid_ids = validated_ids - initiative_req_ids
        if invalid_ids:
            raise ValueError(f"Task {task_data.get('task_id')} references invalid requirements: {invalid_ids}")

        defaults = {
            "initiative": initiative,
            "status": TaskStatus.NOT_STARTED,
            "description": task_data.get("task_description", ""),
            "risk_notes": task_data.get("risk_notes", ""),
            "test_plan": task_data.get("test_plan", ""),
            "completion_criteria": task_data.get("completion_criteria"),
            "requires_test": common.parse_bool(task_data.get("requires_test")),
            "execution_schedule": TaskExecutionSchedule(task_data.get("execution_schedule")),
            "timeout_seconds": task_data.get("timeout_seconds"),
            "task_type": TaskType(task_data.get("task_type")),
            "input_fields": task_data.get("input_fields", {}),
            "output_fields": task_data.get("output_fields", [])
        }

        execution_start_time = task_data.get("execution_start_time")
        if execution_start_time:
            defaults["execution_start_time"] = task_data["execution_start_time"]

        eng_task, created = Task.objects.update_or_create(
            id=task_id if task_id else None,
            defaults=defaults
        )

        # Note: design_handoff handling removed as it's not in the schema

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

        # Only compare schema-defined fields
        fields_to_compare = {
            "description": task_data.get("task_description", ""),
            "risk_notes": task_data.get("risk_notes", ""),
            "test_plan": task_data.get("test_plan", ""),
            "completion_criteria": task_data.get("completion_criteria"),
            "requires_test": common.parse_bool(task_data.get("requires_test")),
            "execution_schedule": task_data.get("execution_schedule"),
            "timeout_seconds": task_data.get("timeout_seconds"),
            "task_type": task_data.get("task_type"),
            "input_fields": task_data.get("input_fields", {}),
            "output_fields": task_data.get("output_fields", []),
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
