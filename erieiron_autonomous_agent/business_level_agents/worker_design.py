from django.db import transaction

from erieiron_autonomous_agent.system_agent_llm_interface import business_level_chat
from erieiron_common.models import Task
from erieiron_common.models import TaskDesignRequirements, DesignComponent


def do_work(task_id):
    task = Task.objects.get(id=task_id)

    chat_data = build_chat_data(task)

    designer = business_level_chat(
        "worker_design.md",
        chat_data,
        debug=True
    )

    process_response(task, designer)


def build_chat_data(task):
    initiative = task.product_initiative

    product_requirements = list(initiative.requirements.all().values(
        "id",
        "summary",
        "acceptance_criteria",
        "testable"
    ))

    triggering_task_data = {
        "task_id": task.id,
        "task_description": task.task_description,
        "validated_requirements": list(task.validated_requirements.values_list("id", flat=True)),
    }

    return {
        "business_name": initiative.business.name,
        "product_initiative_id": initiative.id,
        "product_initiative_title": initiative.title,
        "product_initiative_description": initiative.description,
        "product_requirements": product_requirements,
        "design_task": triggering_task_data
    }


@transaction.atomic
def process_response(task, designer_resp):
    handoff_obj, _ = TaskDesignRequirements.objects.get_or_create(task=task)
    handoff_data = designer_resp.get("design_handoff", {})

    # Component IDs
    components = []
    for comp_id in handoff_data.get("component_ids", []):
        component, _ = DesignComponent.objects.get_or_create(id=comp_id, defaults={"name": comp_id})
        components.append(component)
    handoff_obj.component_ids.set(components)

    # Layout
    layout = handoff_data.get("layout")
    if isinstance(layout, dict):
        handoff_obj.layout = layout
    elif layout is not None:
        raise ValueError(f"Invalid layout structure in design_handoff for task {task.id}")

    # Style Tokens
    style_tokens = handoff_data.get("style_tokens", {})
    if not isinstance(style_tokens, dict):
        raise ValueError(f"Expected style_tokens to be a dictionary for task {task.id}")
    handoff_obj.style_tokens = style_tokens

    handoff_obj.save()
