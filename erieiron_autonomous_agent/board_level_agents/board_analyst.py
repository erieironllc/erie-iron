from erieiron_autonomous_agent.system_agent_llm_interface import board_level_chat
from erieiron_common import common
from erieiron_common.enums import PubSubMessageType
from erieiron_common.message_queue.pubsub_manager import pubsub_workflow, PubSubManager
from erieiron_common.models import Business, BusinessAnalysis, BusinessLegalAnalysis


@pubsub_workflow
def initialize_workflow(pubsub_manager: PubSubManager):
    pubsub_manager.on(
        PubSubMessageType.ANALYSIS_REQUESTED,
        on_analysis_requested,
        PubSubMessageType.ANALYSIS_ADDED
    )


def on_analysis_requested(business_id):
    business = Business.objects.get(id=business_id)
    business_analysis = execute_business_analysis(business)
    execute_legal_analysis(business, business_analysis)


def execute_business_analysis(business) -> BusinessAnalysis:
    business_analysis = board_level_chat(
        "board_analyst--business.md",
        f"""
            Please analyze this Business

            {business.name}
            
            {common.model_to_dict_s(business)}
        """
    )

    analysis = BusinessAnalysis.objects.create(
        business=business,
        business_name=business_analysis.get("business_name"),
        estimated_monthly_revenue_at_steady_state_usd=common.parse_int(business_analysis.get("estimated_monthly_revenue_at_steady_state_usd")),
        time_to_profit_estimate_months=common.parse_int(business_analysis.get("time_to_profit_estimate_months")),
        potential_mode=business_analysis.get("potential_mode"),
        summary=business_analysis.get("summary"),
        final_recommendation_justification=business_analysis.get("final_recommendation", {}).get("justification"),
        final_recommendation_score_1_to_10=common.parse_int(business_analysis.get("final_recommendation", {}).get("score_1_to_10")),
        estimated_operating_total_cost_per_month_usd=common.parse_int(business_analysis.get("operating_expenses", {}).get("estimated_operating_total_cost_per_month_usd")),
        upfront_investment_estimated_amount_usd=common.parse_int(business_analysis.get("upfront_cash_investment_required", {}).get("estimated_amount_usd")),
        total_addressable_market_estimate_usd_per_year=common.parse_int(business_analysis.get("total_addressable_market", {}).get("estimate_usd_per_year")),
        total_addressable_market_source_or_rationale=business_analysis.get("total_addressable_market", {}).get("source_or_rationale"),
        macro_trends_data=business_analysis.get("macro_trends_aligned", []),
        risks_data=business_analysis.get("risks", []),
        monthly_expenses_data=business_analysis.get("operating_expenses", {}).get("monthly_expenses", []),
        potential_competitors_data=business_analysis.get("potential_competitors", []),
        use_of_funds_data=business_analysis.get("upfront_cash_investment_required", {}).get("use_of_funds", []),
    )

    return analysis


def execute_legal_analysis(
        business: Business,
        business_analysis: BusinessAnalysis
):
    legal_analysis = board_level_chat(
        "board_analyst--legal.md",
        f"""
            Please analyze this Business

            {business.name}

            {common.model_to_dict_s(business)}

            {common.model_to_dict_s(business_analysis)}
        """
    )

    BusinessLegalAnalysis.objects.create(
        business=business,
        approved=legal_analysis.get("approved"),
        justification=legal_analysis.get("justification"),
        required_disclaimers_or_terms=legal_analysis.get("required_disclaimers_or_terms"),
        risk_rating=legal_analysis.get("risk_rating"),
    )
