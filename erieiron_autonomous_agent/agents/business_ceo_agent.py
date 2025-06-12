import json
from pathlib import Path

from django.db import transaction

from erieiron_common import bank_utils
from erieiron_common.enums import PubSubMessageType
from erieiron_common.llm_apis import llm_interface
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_common.message_queue.pubsub_manager import pubsub_workflow, PubSubManager
from erieiron_common.models import Business, BusinessCapacityAnalysis, BusinessBankBalanceSnapshot, BusinessBankBalanceSnapshotAccount


@pubsub_workflow
def initialize_workflow(pubsub_manager: PubSubManager):
    pubsub_manager.on(
        PubSubMessageType.BUSINESS_GUIDANCE_UPDATED,
        on_business_guidance_updated
    )


def on_business_guidance_updated(business_id):
    business = Business.objects.get(id=business_id)

    resp = llm_interface.agent_chat([
        LlmMessage.sys(Path("./erieiron_autonomous_agent/prompts/business_ceo_agent.md")),
        LlmMessage.user(
            f"""
            """
        )
    ], debug=True)

    capacity_analysis = resp.json()

    BusinessCapacityAnalysis.objects.create(
        business=business,
        cash_capacity_status=capacity_analysis["cash_capacity_status"],
        compute_capacity_status=capacity_analysis["compute_capacity_status"],
        human_capacity_status=capacity_analysis["human_capacity_status"],
        recommendation=capacity_analysis["recommendation"],
        justification=capacity_analysis.get("justification", "")
    )
