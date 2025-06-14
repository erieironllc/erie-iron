from django.db import transaction

from erieiron_autonomous_agent.system_agent_llm_interface import board_level_chat
from erieiron_common import common
from erieiron_common.enums import PubSubMessageType, BusinessGuidanceRating, BusinessStatus, TrafficLight
from erieiron_common.message_queue.pubsub_manager import PubSubManager
from erieiron_common.models import Business, BusinessGuidance


def exec_board_chair_tasks():
    erieiron_business = Business.get_erie_iron_business()

    for business in Business.objects.all():
        if business.needs_analysis():
            PubSubManager.publish_id(
                PubSubMessageType.ANALYSIS_REQUESTED,
                business.id
            )
        else:
            PubSubManager.publish_id(
                PubSubMessageType.BOARD_GUIDANCE_REQUESTED,
                business.id
            )

    if erieiron_business.needs_capacity_analysis():
        PubSubManager.publish_id(
            PubSubMessageType.RESOURCE_PLANNING_REQUESTED,
            erieiron_business.id
        )
    else:
        erieiron_capacity = erieiron_business.businesscapacityanalysis_set.order_by("created_timestamp").last()
        if TrafficLight.GREEN.eq(erieiron_capacity.recommendation):
            PubSubManager.publish_id(
                PubSubMessageType.PORTFOLIO_ADD_BUSINESSES_REQUESTED,
                erieiron_business.id
            )
        elif TrafficLight.RED.eq(erieiron_capacity.recommendation):
            PubSubManager.publish_id(
                PubSubMessageType.PORTFOLIO_REDUCE_BUSINESSES_REQUESTED,
                erieiron_business.id
            )


def on_board_guidance_requested(business_id):
    erieiron_business = Business.get_erie_iron_business()
    business = Business.objects.get(id=business_id)
    business_analysis, legal_analysis = business.get_latest_analysist()

    business_guidance = board_level_chat(
        "board_chair.md",
        f"""
            Please review and provide guidance for {business.name}
            
            ## Erie Iron Capacity
            {common.model_to_dict_s(erieiron_business.get_latest_capacity())}

            ## {business.name} Business Analysis
            {common.model_to_dict_s(business_analysis)}

            ## {business.name} Legal Analysis
            {common.model_to_dict_s(legal_analysis)}
        """
    )

    process_response(business, business_guidance)


@transaction.atomic
def process_response(business, business_guidance):
    BusinessGuidance.objects.create(
        business=business,
        guidance=business_guidance.get("guidance"),
        justification=business_guidance.get("justification")
    )
    # TODO maybe look at last x number of guidances?
    business_guidance = business.businessguidance_set.order_by("created_timestamp").last()
    if not BusinessGuidanceRating.MAINTAIN.eq(business_guidance.guidance):
        if BusinessGuidanceRating.SHUTDOWN.eq(business_guidance.guidance):
            Business.objects.filter(id=business.id).update(
                status=BusinessStatus.SHUTDOWN
            )
        else:
            Business.objects.filter(id=business.id).update(
                status=BusinessStatus.ACTIVE
            )


def on_business_guidance_updated(business_id):
    print("on_business_guidance_updated", business_id)


def on_portfolio_reduce_businesses_requested(erieiron_business_id):
    pass
