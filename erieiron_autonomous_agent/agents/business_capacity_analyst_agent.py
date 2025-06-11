import json
from pathlib import Path

from erieiron_common.enums import PubSubMessageType
from erieiron_common.llm_apis import llm_interface
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_common.message_queue.pubsub_manager import pubsub_workflow, PubSubManager
from erieiron_common.models import Business, BusinessCapacityAnalysis


@pubsub_workflow
def initialize_workflow(pubsub_manager: PubSubManager):
    pubsub_manager.on(
        PubSubMessageType.BUSINESS_CAPACITY_ANALYSIS_REQUESTED,
        on_business_capacity_analysis_requested
    )


def on_business_capacity_analysis_requested(business_id):
    business = Business.objects.get(id=business_id)

    resp = llm_interface.portfolio_agent_chat([
        LlmMessage.sys(Path("./erieiron_autonomous_agent/prompts/business_capacity_analyst_agent.md")),
        LlmMessage.user(
            f"""
            Please analyze these metrics 

            ## AWS Capacity
            {json.dumps(business.get_aws_capacity(), indent=4)}
            
            ## Budget Capacity
            {json.dumps(business.get_budget_capacity(), indent=4)}
            
            ## Human Capacity
            {json.dumps(business.get_human_capacity(), indent=4)}
            """
        )
    ])

    capacity_analysis = resp.json()

    BusinessCapacityAnalysis.objects.create(
        business=business,
        cash_capacity_status=capacity_analysis["cash_capacity_status"],
        compute_capacity_status=capacity_analysis["compute_capacity_status"],
        human_capacity_status=capacity_analysis["human_capacity_status"],
        recommendation=capacity_analysis["recommendation"],
        justification=capacity_analysis.get("justification", "")
    )
