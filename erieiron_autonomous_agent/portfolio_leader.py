import pprint
from pathlib import Path

from erieiron_common import common
from erieiron_common.enums import PubSubMessageType, BusinessGuidanceRating, BusinessStatus, TrafficLight, BusinessIdeaSource, Constants
from erieiron_common.llm_apis.llm_interface import LlmMessage, portfolio_agent_chat
from erieiron_common.message_queue.pubsub_manager import PubSubManager, pubsub_workflow
from erieiron_common.models import Business, BusinessGuidance

"""
### 1. 🧠 Portfolio Leadership
**Agent**: `Portfolio Leader Agent`

- Acts as Erie Iron’s strategic operator — like Warren Buffett at Berkshire Hathaway
- Operates on a daily/periodic loop to:
  1. Review all **existing businesses**:
     - Request updated business analysis and legal risk reports
     - Shut down underperforming or risky businesses
  2. Assess current **execution capacity** (budget + infrastructure)
  3. If capacity allows:
     - Loop with Business Finder and Analyst until a strong opportunity is found
     - Launch the new business via CEO Agent
  4. If capacity is **insufficient** but the opportunity is strong:
     - Escalate to JJ with rationale, estimated value, and required resources

"""


@pubsub_workflow
def initialize_workflow(pubsub_manager: PubSubManager):
    pubsub_manager.on(
        PubSubMessageType.PORTFOLIO_LEADER_EXEC_REQUESTED,
        exec_portfolio_leader_tasks
    ).on(
        PubSubMessageType.BUSINESS_ANALYSIS_ADDED,
        on_business_analysis_added
    ).on(
        PubSubMessageType.BUSINESS_GUIDANCE_REQUESTED,
        on_business_guidance_requested
    ).on(
        PubSubMessageType.PORTFOLIO_ADD_BUSINESSES_REQUESTED,
        on_portfolio_add_businesses_requested
    ).on(
        PubSubMessageType.PORTFOLIO_REDUCE_BUSINESSES_REQUESTED,
        on_portfolio_reduce_businesses_requested
    )


def exec_portfolio_leader_tasks():
    erieiron_business = Business.get_erie_iron_business()

    for business in Business.objects.all():
        if business.needs_analysis():
            PubSubManager.publish_id(
                PubSubMessageType.BUSINESS_ANALYSIS_REQUESTED,
                business.id
            )
        else:
            PubSubManager.publish_id(
                PubSubMessageType.BUSINESS_GUIDANCE_REQUESTED,
                business.id
            )

    if erieiron_business.needs_capacity_analysis():
        PubSubManager.publish_id(
            PubSubMessageType.BUSINESS_CAPACITY_ANALYSIS_REQUESTED,
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


def on_business_guidance_requested(business_id):
    erieiron_business = Business.get_erie_iron_business()
    business = Business.objects.get(id=business_id)
    business_analysis, legal_analysis = business.get_latest_analysist()

    resp = portfolio_agent_chat([
        LlmMessage.sys(Path("./erieiron_autonomous_agent/prompts/portfolio_buisiness_guidance_agent.md")),
        LlmMessage.user(
            f"""
            Please analyze these metrics for {business.name}
            
            ## Erie Iron Capacity
            {common.model_to_dict_s(erieiron_business.get_latest_capacity())}

            ## {business.name} Business Analysis
            {common.model_to_dict_s(business_analysis)}

            ## {business.name} Legal Analysis
            {common.model_to_dict_s(legal_analysis)}
            """
        )
    ])

    business_guidance = resp.json()

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

        PubSubManager.publish_id(
            PubSubMessageType.BUSINESS_GUIDANCE_UPDATED,
            business.id
        )


def on_portfolio_add_businesses_requested(erieiron_business_id):
    erieiron_business = Business.get_erie_iron_business()
    resp = portfolio_agent_chat([
        LlmMessage.sys(Path("./erieiron_autonomous_agent/prompts/portfolio_business_development_agent.md")),
        LlmMessage.user(
            f"""
            Please find a business that fits this capacity

            ## Erie Iron Capacity
            {common.model_to_dict_s(erieiron_business.get_latest_capacity())}
            """
        )
    ])

    pitch = resp.json().get("pitch")
    pprint.pprint(pitch)
    submit_business_idea(
        idea_content=pitch,
        source=BusinessIdeaSource.BUSINESS_FINDER_AGENT
    )


def on_portfolio_reduce_businesses_requested(erieiron_business_id):
    pass


def submit_business_idea(
        idea_content: str,
        existing_business_id=None,
        source=BusinessIdeaSource.HUMAN
):
    if isinstance(idea_content, Path):
        name = idea_content.name.capitalize().replace("_", " ")
        idea_content = idea_content.read_text()
    else:
        name = f"{Constants.NEW_BUSINESS_NAME_PREFIX} {common.get_now()}"

    business, created = Business.objects.update_or_create(
        id=existing_business_id,
        defaults={
            "name": name,
            "source": source,
            "raw_idea": idea_content
        }
    )

    PubSubManager.publish_id(
        PubSubMessageType.BUSINESS_IDEA_SUBMITTED,
        business.id
    )


def on_business_analysis_added(business_id):
    PubSubManager.publish_id(
        PubSubMessageType.BUSINESS_GUIDANCE_REQUESTED,
        business_id
    )
