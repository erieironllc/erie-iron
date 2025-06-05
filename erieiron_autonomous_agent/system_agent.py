import pprint
from pathlib import Path

from django.db import transaction

from erieiron_common import common
from erieiron_common.enums import LlmModel, SystemAgentTask
from erieiron_common.llm_apis import llm_interface
from erieiron_common.llm_apis import system_prompts
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_common.models import Business, Capability, Goal, ShutdownTrigger


def execute(task: SystemAgentTask, file: str = None):
    if SystemAgentTask.FLESH_OUT_IDEA.eq(task):
        fleshout_business(file)
    elif SystemAgentTask.GENERATE_IDEA.eq(task):
        find_business()
    else:
        raise ValueError(f"unhandled task: {task}")


def find_business():
    messages = [
        system_prompts.BUSINESS_CREATOR,
        LlmMessage.user("Please find a money making business!")
    ]

    for b in Business.objects.exclude(id=Business.get_erie_iron_business().id):
        messages.append(LlmMessage.user(
            f'''
I run an existing business named "{b.name}" which does the following:

"""
{b.summary}
"""

Business names are unique in my system, so do not re-use the business name.  Additionally, please avoid similar businesses that might interfere or canabalize this existing business
'''
        ))

    return exec_business_chat(messages)


def fleshout_business(onepager_file: Path):
    onepager_file = common.assert_exists(onepager_file)

    messages = [
        system_prompts.BUSINESS_CREATOR,
        LlmMessage.user(
            f"""
            Please flesh out this idea:

            {onepager_file.read_text()}
            """
        )
    ]

    business_name = common.get_basename(onepager_file).replace("_", " ").capitalize()

    return exec_business_chat(messages, business_name=business_name)


def send_daily_email():
    # Compose and send JJ the 7am summary
    pass


@transaction.atomic
def exec_business_chat(messages: list[LlmMessage], business_name: str = None) -> Business:
    resp = llm_interface.chat(messages, model=LlmModel.OPENAI_GPT_4o)
    data = resp.json()
    pprint.pprint(data)

    # Create or update the Business
    business, _ = Business.objects.update_or_create(
        name=business_name or data["name"],
        defaults={
            "autonomy_level": data["autonomy_level"],
            "summary": data["summary"],
            "revenue_model": data["revenue_model"],
            "time_to_first_dollar_days": data["time_to_first_dollar_days"],
            "risk_legal": data["risk_assessment"]["legal"],
            "risk_ethical": data["risk_assessment"]["ethical"],
            "risk_regulatory": data["risk_assessment"]["regulatory"],
            "risk_reputation": data["risk_assessment"]["reputation"],
        },
    )

    Goal.objects.filter(business=business).delete()
    for kpi in data["kpis"]:
        Goal.objects.create(
            business=business,
            name=kpi["name"],
            type=kpi["type"],
            target_value=kpi["target_value"],
            evaluation_interval_days=kpi["evaluation_interval_days"],
        )

    ShutdownTrigger.objects.filter(business=business).delete()
    for trigger in data["shutdown_triggers"]:
        ShutdownTrigger.objects.create(
            business=business,
            condition=trigger["condition"],
            severity=trigger["severity"],
        )

    for cap_data in data["required_capabilities"]:
        cap, _ = Capability.objects.get_or_create(
            name=cap_data["name"],
            defaults={
                "description": cap_data["description"],
                "can_build_autonomously": cap_data["can_build_autonomously"],
            }
        )
        business.capabilities.add(cap)

    return business
