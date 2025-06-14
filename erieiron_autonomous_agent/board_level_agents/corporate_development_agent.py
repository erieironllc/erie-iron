import json
from pathlib import Path

from erieiron_autonomous_agent.system_agent_llm_interface import board_level_chat
from erieiron_common import common
from erieiron_common.enums import Constants, BusinessIdeaSource
from erieiron_common.models import Business


def find_new_business_opportunity(erieiron_business_id):
    erieiron_business = Business.get_erie_iron_business()

    messages = []

    for b in Business.objects.exclude(id=erieiron_business.id).exclude(summary__isnull=True):
        messages.append(f"""
            ## Existing Erie Iron Business
            {b.name}
            {b.summary}
        """)

    messages.append(
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
    )

    business_idea = board_level_chat(
        "corporate_development--business_finder.md",
        messages
    )

    return {
        "summary": business_idea.get("summary"),
        "idea_content": business_idea.get("detailed_pitch"),
        "source": BusinessIdeaSource.BUSINESS_FINDER_AGENT,
    }


def submit_business_opportunity(payload):
    existing_business_id = payload.get("existing_business_id")
    summary = payload.get("summary")
    idea_content = payload.get("idea_content")
    source = payload.get("source")

    if isinstance(idea_content, Path):
        name = idea_content.name.capitalize().replace("_", " ")
        idea_content = idea_content.read_text()
    else:
        name = f"{Constants.NEW_BUSINESS_NAME_PREFIX} {common.get_now()}"

    business, created = Business.objects.update_or_create(
        id=existing_business_id,
        defaults={
            "name": name,
            "sandbox_dir_name": common.strip_non_alpha(name).lower(),
            "source": source,
            "raw_idea": idea_content
        }
    )

    business_structure = board_level_chat(
        "corporate_development--business_structurer.md",
        f"""
            Existing business names: {[b.name for b in Business.objects.all()]}
            
            Please structure this business idea:
            
            {summary}
            
            {idea_content}
        """
    )

    if business.name.startswith(Constants.NEW_BUSINESS_NAME_PREFIX.value) and business_structure.get("business_name"):
        Business.objects.filter(id=business.id).update(
            name=business_structure.get("business_name")
        )

    Business.objects.filter(id=business.id).update(
        summary=business_structure.get("summary"),
        sandbox_dir_name=common.strip_non_alpha(business_structure.get("business_name")).lower(),
        business_plan=business_structure.get("business_plan"),
        value_prop=business_structure.get("value_proposition"),
        revenue_model=business_structure.get("monetization"),
        audience=business_structure.get("audience"),
        core_functions=business_structure.get("core_functions", []),
        execution_dependencies=business_structure.get("execution_dependencies", []),
        growth_channels=business_structure.get("growth_channels", []),
        personalization_options=business_structure.get("personalization_options", [])
    )

    return business.id
