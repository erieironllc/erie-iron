"""
# 🧠 Erie Iron - Business Finder Agent
You are the **Business Finder** module of Erie Iron, an autonomous AI platform whose mission is to generate profit legally and ethically.
Erie Iron is currently operating with very limited cash but abundant developer capacity. Your job is to identify business ideas that can be started with near-zero cash investment and generate cash flow as quickly as possible. These early-stage businesses should enable Erie Iron to fund larger, slower, or more capital-intensive projects in the future.

As the **Business Finder**, your role is to explore or flesh out monetizable problems, find investment opportunities, look for underserved niches, or proven models that can be exploited via autonomous systems. You can think outside the box—business ideas do not need to be limited to SAAS models. A business can be **anything** that works toward the goal of making a profit (legally and ethically).
You are tasked with identifying novel, monetizable business ideas that Erie Iron can pursue autonomously. Your output should be self-contained and not dependent on any prior user idea or input.
-
"""
import json
from pathlib import Path

from erieiron_common import common
from erieiron_common.enums import PubSubMessageType, BusinessIdeaSource
from erieiron_common.llm_apis.llm_interface import LlmMessage, portfolio_agent_chat
from erieiron_common.message_queue.pubsub_manager import PubSubManager, pubsub_workflow
from erieiron_common.models import Business


@pubsub_workflow
def initialize_workflow(pubsub_manager: PubSubManager):
    pubsub_manager.on(
        PubSubMessageType.PORTFOLIO_ADD_BUSINESSES_REQUESTED,
        on_portfolio_add_businesses_requested,
        PubSubMessageType.PORTFOLIO_SUBMIT_BUSINESS_IDEA
    )


def on_portfolio_add_businesses_requested(erieiron_business_id):
    erieiron_business = Business.get_erie_iron_business()

    messages = [
        LlmMessage.sys(Path("./erieiron_autonomous_agent/prompts/portfolio_business_development_agent.md")),
    ]

    for b in Business.objects.exclude(id=erieiron_business.id).exclude(summary__isnull=True):
        messages.append(LlmMessage.user(f"""
            ## Existing Erie Iron Business
            {b.name}
            {b.summary}
        """))

    messages.append(LlmMessage.user(
        f"""
            Please find a new business idea that roughly fits this capacity.  
            It's ok to pitch a stretch idea - if we decide we can't take it on, 
            we'll remember it for the future, but if you have a really great idea that
            fits within the capacity, bias towards that.

            ## Erie Iron's current financial position minus reserves
            {json.dumps(erieiron_business.get_new_business_budget_capacity(), indent=4)}
            
            ## Erie Iron Capacity summary
            {common.model_to_dict_s(erieiron_business.get_latest_capacity())}
            """
    ))

    resp = portfolio_agent_chat(messages).json()

    return {
        "summary": resp.get("summary"),
        "idea_content": resp.get("detailed_pitch"),
        "source": BusinessIdeaSource.BUSINESS_FINDER_AGENT,
    }
