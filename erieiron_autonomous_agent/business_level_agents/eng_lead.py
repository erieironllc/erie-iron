from django.db import transaction

from erieiron_autonomous_agent.system_agent_llm_interface import business_level_chat
from erieiron_common.enums import TaskStatus, TaskAssigneeType, PubSubMessageType
from erieiron_common.message_queue.pubsub_manager import PubSubManager
from erieiron_common.models import (
    ProductInitiative,
    Task,
    ProductRequirement,
    TaskDesignRequirements,
    DesignComponent
)

ASSIGNEE_TO_MSGTYPE = {
    TaskAssigneeType.DESIGN: PubSubMessageType.DESIGN_WORK_REQUESTED,
    TaskAssigneeType.ENGINEERING: PubSubMessageType.CODING_WORK_REQUESTED,
    TaskAssigneeType.HUMAN: PubSubMessageType.HUMAN_WORK_REQUESTED,
}


def define_tasks_for_initiative(product_initiative_id):
    initiative = ProductInitiative.objects.get(id=product_initiative_id)
    business = initiative.business

    chat_data = build_chat_data(business, initiative)

    eng_lead_response = business_level_chat("eng_lead.md", chat_data)

    process_response(initiative, eng_lead_response)


def build_chat_data(business, initiative):
    requirements = [
        {
            "id": req.id,
            "summary": req.summary,
            "acceptance_criteria": req.acceptance_criteria,
        } for req in initiative.requirements.all()
    ]

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
            "task_description": task.task_description
        }
        for task in initiative.engineering_tasks.all()
    ]

    return {
        "business_name": business.name,
        "product_initiative_id": initiative.id,
        "product_initiative_title": initiative.title,
        "product_initiative_description": initiative.description,
        "requirements": requirements,
        "linked_kpis": linked_kpis,
        "linked_goals": linked_goals,
        "existing_tasks": existing_tasks,
    }


@transaction.atomic
def process_response(initiative, eng_lead_response):
    updated_or_created_task_ids = []

    for task_data in eng_lead_response.get("engineering_tasks", []):
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

        eng_task, created = Task.objects.update_or_create(
            id=task_id if task_id else None,
            defaults={
                "product_initiative": initiative,
                "status": TaskStatus.NOT_STARTED.value,
                "task_description": task_data.get("task_description", ""),
                "risk_notes": task_data.get("risk_notes", ""),
                "test_plan": task_data.get("test_plan", ""),
                "role_assignee": task_data.get("role_assignee", "ENGINEERING"),
                "completion_criteria": task_data.get("completion_criteria"),
                "raw_llm_payload": task_data,
            }
        )

        # Handle design_handoff for DESIGN tasks
        if eng_task.role_assignee == "DESIGN" and "design_handoff" in task_data:
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
            "task_description": task_data.get("task_description", ""),
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

        print(f"Saved task: {eng_task.id} — {eng_task.task_description[:80]}")

    for task in Task.objects.filter(id__in=updated_or_created_task_ids):
        msg_type = ASSIGNEE_TO_MSGTYPE.get(TaskAssigneeType(task.role_assignee))

        if not msg_type:
            raise Exception(f"unhandled assignee: {task.role_assignee}")

        PubSubManager.publish_id(msg_type, task.id)


def on_work_completed(task_id):
    Task.objects.filter(id=task_id).update(
        status=TaskStatus.COMPLETE
    )
