from pathlib import Path

from erieiron_common.enums import PubSubMessageType, Constants
from erieiron_common.llm_apis import llm_interface
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_common.message_queue.pubsub_manager import pubsub_workflow, PubSubManager
from erieiron_common.models import Business, BusinessStructure


@pubsub_workflow
def initialize_workflow(pubsub_manager: PubSubManager):
    pubsub_manager.on(
        PubSubMessageType.BUSINESS_IDEA_SUBMITTED,
        on_business_idea_submitted,
        PubSubMessageType.BUSINESS_ANALYSIS_REQUESTED
    )


def on_business_idea_submitted(business_id):
    business = Business.objects.get(id=business_id)
    idea_content = business.raw_idea

    resp = llm_interface.portfolio_agent_chat([
        LlmMessage.sys(Path("./erieiron_autonomous_agent/prompts/business_idea_structurer.md")),
        LlmMessage.user(
            f"""
            Please flesh out this idea:

            {idea_content}
            """
        )
    ])
    business_structure = resp.json()

    if business.name.startswith(Constants.NEW_BUSINESS_NAME_PREFIX.value) and business_structure.get("name"):
        Business.objects.filter(id=business.id).update(
            name=business_structure.get("name")
        )

    BusinessStructure.objects.update_or_create(
        business=business,
        defaults={
            "description": business_structure.get("business_plan"),
            "summary": business_structure.get("value_proposition"),
            "revenue_model": business_structure.get("monetization"),
            "audience": business_structure.get("audience"),
            "core_functions": business_structure.get("core_functions", []),
            "execution_dependencies": business_structure.get("execution_dependencies", []),
            "growth_channels": business_structure.get("growth_channels", []),
            "personalization_options": business_structure.get("personalization_options", []),
        }
    )
