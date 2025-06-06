import json
import pprint
from pathlib import Path

from django.db import transaction

from erieiron_common import common
from erieiron_common.enums import LlmModel, SystemAgentTask
from erieiron_common.llm_apis import llm_interface
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_common.models import Business, Goal, ShutdownTrigger


def execute(task: SystemAgentTask, arg: str = None):
    if SystemAgentTask.FLESH_OUT_IDEA.eq(task):
        fleshout_business(arg)
    elif SystemAgentTask.GENERATE_IDEA.eq(task):
        find_business()
    elif SystemAgentTask.IDENTIFY_CAPABILITIES.eq(task):
        identify_capabilities(arg)
    else:
        raise ValueError(f"unhandled task: {task}")


def identify_capabilities(business_id):
    business = Business.objects.get(id=business_id)

    messages = [
        LlmMessage.sys(Path("./prompts/capability_identifier.md")),
        LlmMessage.user(f"""
Identify required capabilities to build and run this business:
Business Name:  {business.name}
Summary:
{business.summary}
        """)
    ]

    resp = llm_interface.chat(messages, model=LlmModel.OPENAI_GPT_4o)
    data = resp.json()
    pprint.pprint(data)

    # for cap_data in data["required_capabilities"]:
    #     cap, _ = Capability.objects.get_or_create(
    #         name=cap_data["name"],
    #         defaults={
    #             "description": cap_data["description"],
    #             "can_build_autonomously": cap_data["can_build_autonomously"],
    #         }
    #     )
    #     business.capabilities.add(cap)

    pass


def find_business():
    messages = [
        LlmMessage.sys(Path("./prompts/business_finder.md")),
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

    resp = llm_interface.chat([
        LlmMessage.sys(Path("./prompts/business_idea_structurer.md")),
        LlmMessage.user(
            f"""
            Please flesh out this idea:

            {onepager_file.read_text()}
            """
        )
    ], model=LlmModel.OPENAI_GPT_4o)
    business_structure = resp.json()

    print("business plan structure")
    pprint.pprint(business_structure)

    resp = llm_interface.chat([
        LlmMessage.sys(Path("./prompts/business_analyst.md")),
        LlmMessage.user(json.dumps(business_structure, indent=4))
    ], model=LlmModel.OPENAI_GPT_4o)
    business_analysis = resp.json()

    print("business analysis")
    pprint.pprint(business_analysis)

    resp = llm_interface.chat([
        LlmMessage.sys(Path("./prompts/capability_identifier.md")),
        LlmMessage.user(json.dumps(business_structure, indent=4))
    ], model=LlmModel.OPENAI_GPT_4o)
    capabilities_analysis = resp.json()

    print("required capabilities")
    pprint.pprint(capabilities_analysis)

    # business_name = common.get_basename(onepager_file).replace("_", " ").capitalize()
    # return exec_business_chat(messages, business_name=business_name)


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

    return business
