import textwrap

from django.db import transaction

from erieiron_autonomous_agent.enums import BusinessGuidanceRating, BusinessStatus, TrafficLight
from erieiron_autonomous_agent.models import Business
from erieiron_autonomous_agent.models import BusinessGuidance
from erieiron_autonomous_agent.system_agent_llm_interface import board_level_chat
from erieiron_common import common
from erieiron_common.enums import PubSubMessageType, LlmModel, LlmReasoningEffort, LlmVerbosity
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_common.message_queue.pubsub_manager import PubSubManager


def exec_board_chair_tasks():
    erieiron_business = Business.get_erie_iron_business()
    
    erieiron_capacity = erieiron_business.businesscapacityanalysis_set.order_by("created_timestamp").last()
    if erieiron_capacity:
        if TrafficLight.GREEN.eq(erieiron_capacity.recommendation):
            PubSubManager.publish_id(PubSubMessageType.PORTFOLIO_ADD_BUSINESSES_REQUESTED)
        elif TrafficLight.RED.eq(erieiron_capacity.recommendation):
            PubSubManager.publish_id(
                PubSubMessageType.PORTFOLIO_REDUCE_BUSINESSES_REQUESTED,
                erieiron_business.id
            )


def exec_business_analysis():
    for business in Business.objects.all():
        if business.needs_capacity_analysis():
            PubSubManager.publish_id(
                PubSubMessageType.RESOURCE_PLANNING_REQUESTED,
                business.id
            )
        
        if business.needs_analysis():
            PubSubManager.publish_id(
                PubSubMessageType.ANALYSIS_REQUESTED,
                business.id
            )


def on_board_guidance_requested(business_id):
    erieiron_business = Business.get_erie_iron_business()
    if erieiron_business.id == business_id:
        return None
    
    business = Business.objects.get(id=business_id)
    business_analysis, legal_analysis = business.get_latest_analysist()
    
    business_guidance = board_level_chat(
        "Board Chair Guidance",
        "board_chair.md",
        f"""
            Please review and provide guidance for {business.name}
            
            ## Erie Iron Capacity
            {common.model_to_dict_s(erieiron_business.get_latest_capacity())}

            ## {business.name} Business Analysis
            {common.model_to_dict_s(business_analysis)}

            ## {business.name} Legal Analysis
            {common.model_to_dict_s(legal_analysis)}
        """,
        business=business
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
        # else:
        #     Business.objects.filter(id=business.id).update(
        #         status=BusinessStatus.ACTIVE
        #     )


def on_business_guidance_updated(business_id):
    print("on_business_guidance_updated", business_id)


def on_portfolio_reduce_businesses_requested(erieiron_business_id):
    pass


def on_portfolio_pick_new_business(payload):
    """
    Evaluate all business ideas and select the top 3 best ones
    based on provided guidance and Erie Iron constraints.
    
    Returns:
        dict: Structured response with top 3 ranked businesses and analysis
    """
    # Get all idea-stage businesses
    idea_businesses = Business.objects.filter(
        niche_category__isnull=False
    ).exclude(
        status=BusinessStatus.ACTIVE
    ).exclude(id__in=[
        "64621728-ffae-45d9-a98a-8804295cbebe",
        "47b33760-3d41-4ef6-8f4b-b1a1a97ed1ea",
        "8782fab6-5dfb-411a-b41d-2929e10a4395"
    ])
    guidance = payload.get("guidance")
    
    if not idea_businesses.exists():
        return {
            "error": "No business ideas available for selection",
            "guidance": guidance
        }
    
    # Prepare comprehensive business context
    business_context = []
    for business in idea_businesses:
        business_analysis, legal_analysis = business.get_latest_analysist()
        business_context.append({
            "business_id": business.id,
            "niche_category": business.niche_category,
            "summary": business.summary,
            "revenue_model": business.revenue_model,
            "audience": business.audience,
            'business_analysis': common.get_dict(business_analysis),
            'legal_analysis': common.get_dict(legal_analysis)
        })
    
    user_messages = [
        LlmMessage.user_from_data("Business Ideas to Evaluate", business_context, "business"),
        textwrap.dedent(f"""
        ## GUIDANCE FOR BUSINESS SELECTION
        {guidance or 'Pick a winner'}
        """)
    ]
    
    response = board_level_chat(
        "Board Chair Business Picker",
        "board_chair--business_picker.md",
        user_messages,
        reasoning_effort=LlmReasoningEffort.HIGH,
        verbosity=LlmVerbosity.LOW,
        model=LlmModel.OPENAI_GPT_5_1
    )
    
    return response
