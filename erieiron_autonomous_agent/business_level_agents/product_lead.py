from django.db import transaction

from erieiron_autonomous_agent.system_agent_llm_interface import business_level_chat
from erieiron_autonomous_agent.models import Business
from erieiron_autonomous_agent.models import BusinessCapacityAnalysis, BusinessKPI, BusinessGoal, BusinessCeoDirective, Initiative, ProductRequirement


def define_initiatives(business_id):
    business = Business.objects.get(id=business_id)

    chat_data = build_chat_data(business)

    product_lead_response = business_level_chat(
        "product_lead.md",
        chat_data
    )

    return process_response(
        business,
        product_lead_response
    )


def define_single_initiative(initiative_id):
    initiative = Initiative.objects.get(id=initiative_id)

    product_lead_response = business_level_chat(
        "product_lead--single_intiative.md",
        f"""
{initiative.title} ({initiative.initiative_type} initiative)

{initiative.description}
        """
    )

    ingest_initiative_data(
        initiative.business,
        product_lead_response,
        initiative_id=initiative_id
    )


def build_chat_data(business):
    kpis = list(BusinessKPI.objects.filter(business=business).values(
        "kpi_id", "name", "description", "target_value", "unit", "priority"
    ))

    goals = list(BusinessGoal.objects.filter(business=business).values(
        "goal_id", "kpi_id", "description", "target_value", "unit", "due_date", "priority", "status"
    ))
    
    directives = list(BusinessCeoDirective.objects.filter(
        business=business, target_agent="ProductAgent"
    ).values(
        "directive_summary", "goal_alignment", "kpi_targets"
    ))

    capacity = BusinessCapacityAnalysis.objects.filter(business=business).last()
    capacity_summary = capacity.recommendation if capacity else "No capacity analysis available."

    initiatives = list(Initiative.objects.filter(business=business).values(
        "id", "title", "description", "priority"
    ))

    requirements = list(ProductRequirement.objects.filter(
        initiative__business=business
    ).values(
        "id", "summary", "acceptance_criteria", "testable", "initiative_id"
    ))

    return {
        "business_name": business.name,
        "business_summary": business.summary,
        "business_pitch": business.raw_idea,
        "kpis": kpis,
        "goals": goals,
        "ceo_directives": directives,
        "capacity_summary": capacity_summary,
        "existing_initiatives": initiatives,
        "existing_requirements": requirements
    }


@transaction.atomic()
def process_response(business, product_lead_response):
    updated_or_created_initiative_ids = []

    for initiative_data in product_lead_response.get("initiatives", []):
        initiative = ingest_initiative_data(
            business,
            initiative_data
        )
        updated_or_created_initiative_ids.append(initiative.id)

    return updated_or_created_initiative_ids


def ingest_initiative_data(business, initiative_data, initiative_id=None):
    updated_or_created_initiative_ids = []
    initiative_token = initiative_data["initiative_token"]
    
    initiative, created = Initiative.objects.update_or_create(
        id=initiative_id or initiative_token,
        defaults={
            "business": business,
            "title": initiative_data["title"],
            "description": initiative_data["description"],
            "priority": initiative_data["priority"],
            "expected_kpi_lift": initiative_data.get("expected_kpi_lift", {}),
        }
    )
    
    if created or any(getattr(initiative, k) != v for k, v in {
        "title": initiative_data["title"],
        "description": initiative_data["description"],
        "priority": initiative_data["priority"],
        "expected_kpi_lift": initiative_data.get("expected_kpi_lift", {})
    }.items()):
        updated_or_created_initiative_ids.append(initiative.id)

    initiative.linked_kpis.set(
        list(BusinessKPI.objects.filter(kpi_id__in=initiative_data.get("linked_kpis", [])))
    )
    initiative.linked_goals.set(
        list(BusinessGoal.objects.filter(goal_id__in=initiative_data.get("linked_goals", [])))
    )
    for req in initiative_data.get("requirements", []):
        requirement_token = req["requirement_token"]
        ProductRequirement.objects.update_or_create(
            id=requirement_token,
            defaults={
                "initiative": initiative,
                "summary": req["summary"],
                "acceptance_criteria": req["acceptance_criteria"],
                "testable": req["testable"]
            }
        )

    return initiative
