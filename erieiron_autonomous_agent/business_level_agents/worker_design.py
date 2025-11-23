from django.db import transaction

from erieiron_autonomous_agent.models import Business, Task
from erieiron_autonomous_agent.system_agent_llm_interface import business_level_chat, get_reasoning_model
from erieiron_common.llm_apis.llm_interface import LlmMessage


def do_work(task_id):
    task = Task.objects.get(id=task_id)
    if not task.allow_execution():
        return
    
    from erieiron_autonomous_agent.business_level_agents import worker_coder
    worker_coder.do_work(task_id)
    # chat_data = build_chat_data(task)
    # 
    # designer = business_level_chat(
    #     task.initiative,
    #     "Do Designer Work",
    #     "worker_design.md",
    #     chat_data,
    #     debug=True
    # )
    # 
    # process_response(task, designer)


def build_chat_data(task):
    initiative = task.initiative
    
    product_requirements = list(initiative.requirements.all().values(
        "id",
        "summary",
        "acceptance_criteria",
        "testable"
    ))
    
    triggering_task_data = {
        "task_id": task.id,
        "description": task.description,
        "validated_requirements": list(task.validated_requirements.values_list("id", flat=True)),
    }
    
    return {
        "business_name": initiative.business.name,
        "initiative_id": initiative.id,
        "initiative_title": initiative.title,
        "initiative_description": initiative.description,
        "product_requirements": product_requirements,
        "design_task": triggering_task_data
    }



def create_business_design_spec(business_id):
    business = Business.objects.get(id=business_id)
    data = business.get_llm_data()
    data.pop("business_analysis", None)
    data.pop("legal_analysis", None)
    data.pop("critical_evaluations", None)
    
    resp = business_level_chat(
        business,
        "Generate Design Spec",
        "ui_designer.md",
        [
            LlmMessage.user_from_data("Business needing the UI spec", data)
        ],
        text_output=True,
        model=get_reasoning_model()
    )
    
    business.ui_design_spec = resp
    business.save()
