from django.db import transaction

from erieiron_autonomous_agent.system_agent_llm_interface import business_level_chat
from erieiron_common.models import Business, BusinessCapacityAnalysis
from erieiron_common.models import BusinessKPI, BusinessGoal, BusinessCeoDirective
from erieiron_common.models import ProductInitiative, ProductRequirement


def define_product_initiatives(business_id):
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
        "directive_summary", "goal_alignment", "kpi_targets", "initiative_reference"
    ))
    capacity = BusinessCapacityAnalysis.objects.filter(business=business).last()
    capacity_summary = capacity.recommendation if capacity else "No capacity analysis available."
    initiatives = list(ProductInitiative.objects.filter(business=business).values(
        "id", "title", "description", "priority"
    ))
    requirements = list(ProductRequirement.objects.filter(
        product_initiative__business=business
    ).values(
        "id", "summary", "acceptance_criteria", "testable", "product_initiative_id"
    ))
    chat_data = {
        "business_name": business.name,
        "business_summary": business.summary,
        "kpis": kpis,
        "goals": goals,
        "ceo_directives": directives,
        "capacity_summary": capacity_summary,
        "existing_initiatives": initiatives,
        "existing_requirements": requirements
    }
    return chat_data


@transaction.atomic()
def process_response(business, product_lead_response):
    updated_or_created_initiative_ids = []

    for initiative_data in product_lead_response.get("product_initiatives", []):
        initiative_token = initiative_data["initiative_token"]
        initiative, created = ProductInitiative.objects.update_or_create(
            id=initiative_token,
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
                    "product_initiative": initiative,
                    "summary": req["summary"],
                    "acceptance_criteria": req["acceptance_criteria"],
                    "testable": req["testable"]
                }
            )

    return updated_or_created_initiative_ids
