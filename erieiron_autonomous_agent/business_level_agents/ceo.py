import logging

from django.db import transaction

from erieiron_autonomous_agent.enums import BusinessStatus, BusinessOperationType
from erieiron_autonomous_agent.system_agent_llm_interface import business_level_chat
from erieiron_common import common
from erieiron_autonomous_agent.models import Business
from erieiron_autonomous_agent.models import BusinessKPI, BusinessGoal, BusinessCeoDirective
from erieiron_common.enums import PubSubMessageType
from erieiron_common.message_queue.pubsub_manager import PubSubManager


def on_business_guidance_updated(business_id):
    if Business.get_erie_iron_business().id == business_id:
        return

    business = Business.objects.get(id=business_id)
    
    if BusinessStatus.IDEA.eq(business.status):
        logging.info(f"skipping ceo guidance generation - status = {business.status}")
        return

    chat_data = build_chat_data(business)

    ceo_analysis = business_level_chat(
        business,
        "CEO Guidance",
        "ceo.md",
        chat_data
    )

    process_response(business, ceo_analysis)
    
    PubSubManager.publish_id(
        PubSubMessageType.BUSINESS_BOOTSTRAP_REQUESTED,
        business.id
    )


def build_chat_data(business):
    business_analysis, legal_analysis = business.get_latest_analysist()
    product_status = {
        "summary": "Not started.  No product features have been defined.  Blocked by no "
    }
    eng_status = {
        "summary": "No engineering started or in progress. Blocked by product"
    }
    sales_status = {
        "summary": "No Sales or Marketing initiatives started or in progress. Blocked by not having a product in market"
    }
    chat_data = [
        f"## Board Guidance\n{common.model_to_dict_s(business.get_latest_board_guidance())}",
        f"## Business Analysis\n{common.model_to_dict_s(business_analysis)}",
        f"## Legal Analysis\n{common.model_to_dict_s(legal_analysis)}",
        f"## Current KPI status\n{business.get_kpis_status()}",
        f"## Current Goals status\n{business.get_goals_status()}",
        f"## Sales and Marketing Lead status update\n{sales_status}",
        f"## Product Lead status update\n{product_status}",
        f"## Engineering Lead status update\n{eng_status}",
        "Please evaluate the Board Guidance, evaluate the business updates, and issue directives (if necessary)"
    ]
    return chat_data


@transaction.atomic()
def process_response(business, ceo_analysis):
    for kpi_data in ceo_analysis.get("kpis", []):
        BusinessKPI.objects.update_or_create(
            business=business,
            kpi_id=kpi_data["kpi_id"],
            defaults={
                "name": kpi_data["name"],
                "description": kpi_data.get("description"),
                "target_value": kpi_data["target_value"],
                "unit": kpi_data["unit"],
                "priority": kpi_data["priority"]
            }
        )
    for goal_data in ceo_analysis.get("goals", []):
        kpi = BusinessKPI.objects.get(business=business, kpi_id=goal_data["kpi_id"])
        BusinessGoal.objects.update_or_create(
            business=business,
            goal_id=goal_data["goal_id"],
            defaults={
                "kpi": kpi,
                "description": goal_data["description"],
                "target_value": goal_data["target_value"],
                "unit": goal_data["unit"],
                "due_date": goal_data["due_date"],
                "priority": goal_data["priority"],
                "status": goal_data["status"]
            }
        )
    for directive in ceo_analysis.get("ceo_directives", []):
        BusinessCeoDirective.objects.update_or_create(
            business=business,
            target_agent=directive["target_agent"],
            directive_summary=directive["directive_summary"],
            defaults={
                "goal_alignment": directive.get("goal_alignment", []),
                "kpi_targets": directive.get("kpi_targets", {})
            }
        )
