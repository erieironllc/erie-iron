from erieiron_autonomous_agent.system_agent_llm_interface import business_level_chat
from erieiron_common.enums import PubSubMessageType
from erieiron_common.message_queue.pubsub_manager import pubsub_workflow, PubSubManager
from erieiron_common.models import Business, BusinessCapacityAnalysis


# @pubsub_workflow
# def initialize_workflow(pubsub_manager: PubSubManager):
#     pubsub_manager.on(
#         PubSubMessageType.BOARD_GUIDANCE_UPDATED,
#         on_business_guidance_updated
#     )


def on_business_guidance_updated(business_id):
    business = Business.objects.get(id=business_id)

    resp = business_level_chat(
        "business_ceo_agent.md",
        f"""
        """
    )

    capacity_analysis = resp.json()

    BusinessCapacityAnalysis.objects.create(
        business=business,
        cash_capacity_status=capacity_analysis["cash_capacity_status"],
        compute_capacity_status=capacity_analysis["compute_capacity_status"],
        human_capacity_status=capacity_analysis["human_capacity_status"],
        recommendation=capacity_analysis["recommendation"],
        justification=capacity_analysis.get("justification", "")
    )
