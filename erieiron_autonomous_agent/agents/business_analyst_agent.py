import pprint
from pathlib import Path

from erieiron_common import common
from erieiron_common.enums import PubSubMessageType
from erieiron_common.llm_apis import llm_interface
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_common.message_queue.pubsub_manager import pubsub_workflow, PubSubManager
from erieiron_common.models import Business, BusinessAnalysis, MacroTrend, Risk, MonthlyExpense, PotentialCompetitor, UseOfFunds


@pubsub_workflow
def initialize_workflow(pubsub_manager: PubSubManager):
    pubsub_manager.on(
        PubSubMessageType.BUSINESS_STRUCTURE_UPDATED,
        on_business_structure_updated
    )


def on_business_structure_updated(business_id):
    business = Business.objects.get(id=business_id)
    resp = llm_interface.chat([
        LlmMessage.sys(Path("./prompts/business_analyst.md")),
        LlmMessage.user(
            f"""
            Please analyze this Business

            {common.get_dict(business)}
            """
        )
    ])
    business_analysis = resp.json()

    # Save the business_analysis data on the BusinessAnalysis (and related) models

    analysis = BusinessAnalysis.objects.create(
        business=business,
        business_name=business_analysis.get("business_name"),
        estimated_monthly_revenue_at_steady_state_usd=business_analysis.get("estimated_monthly_revenue_at_steady_state_usd"),
        time_to_profit_estimate_months=business_analysis.get("time_to_profit_estimate_months"),
        potential_mode=business_analysis.get("potential_mode"),
        summary=business_analysis.get("summary"),
        final_recommendation_justification=business_analysis.get("final_recommendation", {}).get("justification"),
        final_recommendation_score_1_to_10=business_analysis.get("final_recommendation", {}).get("score_1_to_10"),
        estimated_operating_total_cost_per_month_usd=business_analysis.get("operating_expenses", {}).get("estimated_operating_total_cost_per_month_usd"),
        upfront_investment_estimated_amount_usd=business_analysis.get("upfront_cash_investment_required", {}).get("estimated_amount_usd"),
        total_addressable_market_estimate_usd_per_year=business_analysis.get("total_addressable_market", {}).get("estimate_usd_per_year"),
        total_addressable_market_source_or_rationale=business_analysis.get("total_addressable_market", {}).get("source_or_rationale"),
    )

    for trend in business_analysis.get("macro_trends_aligned", []):
        MacroTrend.objects.create(business_analysis=analysis, description=trend)

    for risk in business_analysis.get("risks", []):
        Risk.objects.create(business_analysis=analysis, description=risk)

    for exp in business_analysis.get("operating_expenses", {}).get("monthly_expenses", []):
        MonthlyExpense.objects.create(
            business_analysis=analysis,
            name=exp.get("name"),
            purpose=exp.get("purpose"),
            monthly_expense_usd=exp.get("monthly_expense_usd")
        )

    for comp in business_analysis.get("potential_competitors", []):
        PotentialCompetitor.objects.create(
            business_analysis=analysis,
            name=comp.get("name"),
            notes=comp.get("notes"),
            url=comp.get("url")
        )

    for fund in business_analysis.get("upfront_cash_investment_required", {}).get("use_of_funds", []):
        UseOfFunds.objects.create(business_analysis=analysis, description=fund)
    pprint.pprint(business_analysis)

    # PubSubManager.publish_id(
    #     PubSubMessageType.BUSINESS_STRUCTURE_UPDATED,
    #     business.id
    # )
