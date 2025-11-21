from erieiron_autonomous_agent.models import Business, BusinessSecondOpinionEvaluation
from erieiron_autonomous_agent.models import BusinessAnalysis, BusinessLegalAnalysis, BusinessHumanJobDescription
from erieiron_autonomous_agent.system_agent_llm_interface import board_level_chat
from erieiron_common import common
from erieiron_common.enums import LlmModel, LlmReasoningEffort
from erieiron_common.llm_apis.llm_interface import LlmMessage


def on_analysis_requested(business_id):
    business = Business.objects.get(id=business_id)
    business_analysis = execute_business_analysis(business)
    execute_legal_analysis(business, business_analysis)
    define_human_job_descriptions(business_id)
    perform_second_opinion_evaluation(business_id)


def perform_second_opinion_evaluation(business_id):
    erieiron_business = Business.get_erie_iron_business()
    business = Business.objects.get(id=business_id)
    business_analysis, legal_analysis = business.get_latest_analysist()
    business_data = business.get_llm_data()
    business_data.pop("critical_evaluations", None)
    
    for llm_model in [LlmModel.CLAUDE_4_5, LlmModel.OPENAI_GPT_5_1]:
        evaluation = board_level_chat(
            "Business Second Opinion Evaluation",
            "business--second_opinion_evaluator.md",
            LlmMessage.user_from_data(
                "Please evaluate this business",
                business_data
            ),
            business=business,
            reasoning_effort=LlmReasoningEffort.HIGH,
            verbosity=LlmReasoningEffort.MEDIUM,
            model=llm_model
        )
        
        BusinessSecondOpinionEvaluation.objects.create(
            business=business,
            llm_model=llm_model,
            evaluation=evaluation
        )


def execute_business_analysis(business) -> BusinessAnalysis:
    business_analysis = board_level_chat(
        "Business Analysis",
        "board_analyst--business.md",
        f"""
            Please analyze this Business

            {business.name}
            
            {common.model_to_dict_s(business)}
        """,
        business=business
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
        "Legal Analysis",
        "board_analyst--legal.md",
        f"""
            Please analyze this Business

            {business.name}

            {common.model_to_dict_s(business)}

            {common.model_to_dict_s(business_analysis)}
        """,
        business=business
    )
    
    BusinessLegalAnalysis.objects.create(
        business=business,
        approved=legal_analysis.get("approved"),
        justification=legal_analysis.get("justification"),
        required_disclaimers_or_terms=legal_analysis.get("required_disclaimers_or_terms"),
        risk_rating=legal_analysis.get("risk_rating"),
        recommended_entity_structure=legal_analysis.get("recommended_entity_structure"),
        entity_structure_justification=legal_analysis.get("entity_structure_justification")
    )


def define_human_job_descriptions(business_id):
    """
    Define human job descriptions for a business based on available human capacity.
    
    Args:
        business_id: Business ID to analyze
    """
    business = Business.objects.get(id=business_id)
    
    # Clear existing job descriptions for this business
    BusinessHumanJobDescription.objects.filter(business=business).delete()
    
    job_analysis = board_level_chat(
        "Human Job Description Analysis",
        "board_analyst--human_job_descriptions.md",
        [
            LlmMessage.user_from_data("Business Definition", business.get_llm_data()),
            "Please define human job descriptions for this business"
        ],
        business=business
    )
    
    # Create job description records if human labor is required
    if job_analysis.get("requires_human_labor", False):
        for job_desc in job_analysis.get("human_job_descriptions", []):
            BusinessHumanJobDescription.objects.create(
                business=business,
                estimated_hours_per_week=job_desc.get("estimated_hours_per_week", 0),
                job_description=f"""# {job_desc.get('role_title', 'Untitled Role')}

{job_desc.get('job_description', '')}

## Justification
{job_desc.get('justification', '')}
"""
            )
    
    return job_analysis
