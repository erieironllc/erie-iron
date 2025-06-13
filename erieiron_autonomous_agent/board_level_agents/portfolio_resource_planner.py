import json

from django.db import transaction

from erieiron_autonomous_agent.system_agent_llm_interface import board_level_chat
from erieiron_common import bank_utils
from erieiron_common.enums import PubSubMessageType
from erieiron_common.message_queue.pubsub_manager import pubsub_workflow, PubSubManager
from erieiron_common.models import Business, BusinessCapacityAnalysis, BusinessBankBalanceSnapshot, BusinessBankBalanceSnapshotAccount



def on_resource_planning_requested(business_id):
    business = Business.objects.get(id=business_id)

    # TODO move this to a daily job
    if business.needs_bank_balance_update():
        balance_data = bank_utils.get_account_balance_data()
        with transaction.atomic():
            balance_snapshot = BusinessBankBalanceSnapshot.objects.create(business=business)
            for account_data in balance_data.get("accounts"):
                BusinessBankBalanceSnapshotAccount.objects.create(
                    snapshot=balance_snapshot,
                    account_name=account_data.get("legalBusinessName", 0),
                    account_id=account_data.get("accountNumber", 0),
                    available_balance=account_data.get("availableBalance", 0),
                    current_balance=account_data.get("currentBalance", 0),
                    status=account_data.get("status", "")
                )

    capacity_analysis = board_level_chat(
        "portfolio_resource_planner.md",
        f"""
            Please analyze these metrics give resource planning guidance

            ## AWS Capacity
            {json.dumps(business.get_aws_capacity(), indent=4)}
            
            ## Budget Capacity
            {json.dumps(business.get_budget_capacity(), indent=4)}
            
            ## Human Capacity
            {json.dumps(business.get_human_capacity(), indent=4)}
        """
    )

    BusinessCapacityAnalysis.objects.create(
        business=business,
        cash_capacity_status=capacity_analysis["cash_capacity_status"],
        compute_capacity_status=capacity_analysis["compute_capacity_status"],
        human_capacity_status=capacity_analysis["human_capacity_status"],
        recommendation=capacity_analysis["recommendation"],
        justification=capacity_analysis.get("justification", "")
    )
