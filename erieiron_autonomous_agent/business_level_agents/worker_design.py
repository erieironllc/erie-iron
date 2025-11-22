from erieiron_autonomous_agent.models import Business
from erieiron_autonomous_agent.system_agent_llm_interface import business_level_chat, get_reasoning_model
from erieiron_common.llm_apis.llm_interface import LlmMessage


def do_work(business_id):
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
