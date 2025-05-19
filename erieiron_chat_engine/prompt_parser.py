import logging
import random
from typing import List, Optional, Type

from django.db.models import QuerySet

from erieiron_chat_engine import language_utils
from erieiron_chat_engine.base_chat_channel import BaseChatChannel
from erieiron_chat_engine.chat_channels.llm_chat_channel import LlmChatChannel
from erieiron_chat_engine.language_utils import SYSTEM_COMMAND_TYPE_TO_EXAMPLE_PHRASE, MAP_INTENT_INTENTDESC
from erieiron_chat_engine.prompt import Prompt
from erieiron_common import common, models
from erieiron_common.enums import PromptIntent, PubSubMessageType
from erieiron_common.llm_apis import prompt_engineered_utils
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_common.models import ProjectInteraction, Project, Person
from erieiron_common.runtime_config import RuntimeConfig
from json_encoder import ErieIronJSONEncoder

MESSAGE_TO_CHAT_CHANNEL_CLS = {
    PubSubMessageType.CHAT_CHANNEL_LLM: LlmChatChannel,
}
CHAT_CHANNEL_CLS_TO_MESSAGE = {v: k for k, v in MESSAGE_TO_CHAT_CHANNEL_CLS.items()}


def parse_prompt(
        person: models.Person,
        previous_interactions: QuerySet[ProjectInteraction],
        interaction: ProjectInteraction
) -> Prompt:
    prompt_text = interaction.prompt
    normalized_prompt = Prompt.normalize_text(prompt_text)

    count_user_initiated_interactions = get_count_user_initiated_interactions(
        previous_interactions
    )

    try:
        intent_options = [i for i in PromptIntent if i != PromptIntent.ERIE_GENERATED]
        intent_options = [i for i in intent_options if i != PromptIntent.CONTINUE_PREVIOUS_CONVERSATION]

        resp = llm_parse_prompt(
            interaction,
            intent_options,
            previous_interactions
        )

        continue_conversation = parse_continue_conversation(
            resp,
            prompt_text,
            previous_interactions
        )

        intent = parse_intent(
            resp,
            person,
            intent_options
        )

        is_command = language_utils.is_command(normalized_prompt)
        is_question = language_utils.is_question(normalized_prompt)

        system_command_tuples = parse_system_command(resp)

        return Prompt(
            person_id=person.id,
            text=prompt_text,
            count=min(5, int(common.get(resp, "count", 1))),
            intent=intent,
            continue_conversation=continue_conversation,
            count_user_initiated_interactions=count_user_initiated_interactions,
            is_question=is_question,
            is_command=is_command
        )
    except Exception as e:
        logging.exception(e)

        return Prompt(
            person_id=person.id,
            text=prompt_text,
            intent=PromptIntent.UNKNOWN,
            continue_conversation=False,
            count_user_initiated_interactions=count_user_initiated_interactions,
            count=1,
            is_question=False,
            is_command=False
        )


def llm_parse_prompt(
        interaction,
        intent_options,
        previous_interactions
):
    prompt_text = interaction.prompt
    intent_option_string = '\n'.join([f"{idx}: {MAP_INTENT_INTENTDESC[prompt_intent]}" for idx, prompt_intent in enumerate(intent_options)])
    prompt_messages = [
        LlmMessage.sys(f"""
You are an expert at parsing user prompts and understanding the best system component to handle the prompts
Your task is to tag the latest prompt with the following attributes: is_responding, intent_idx, content_type_idx, count, system_command

# "is_responding" tag
The first tag (name=is_responding) is to indicate if you think the Artist's latest prompt is responding to question YOU ASKED IN YOUR PREVIOUS PROMPT. (is responding=1 | is not responding=0) 

# "intent_idx" tag
The second tag (name=intent_idx) identifies the Artist's intent with their most recent prompt.  Which of the follow most closely describes what you think their intent is?
{intent_option_string}
RESPOND WITH ONE OF THE ABOVE INTENT INDEXES

# "count" tag
the fouth tag identifies the count of things the Artist is looking for (name=count).  If you cannot derive a count from there text, assume count is 1

# "system_command" tag
the fifth tag identifies if the prompt is a "system command".  The system support the following "system commands":  {", ".join([k.value for k in SYSTEM_COMMAND_TYPE_TO_EXAMPLE_PHRASE])}
the following data structure shows example phrases for each of these system commands:
{ErieIronJSONEncoder.dumps({str(k): str(v) for k, v in SYSTEM_COMMAND_TYPE_TO_EXAMPLE_PHRASE.items()})}
## "system_command" rules
    * evaluate the prompt and identify if you think the user is requesting a system command.  if you think they are requesting a system command set the command_type and command_value on the system_command key
    * only set a value for the system_command key if are 200% sure they are requesting one of the supported system commands.  It's ok to miss a system command, but it's bad to think it's a system command when it's not.  false positives are much worse than false negatives
    * only reply with the 'send_help' system_command if you think the user wants help with Collaya functionality.  Do not reply with send_help if you think they want help with a music idea or song section
    * If you think prompt text represents a system command return the set the command_type key to the command type and the command_value key to the related value for XXX
""")
    ]
    for idx, i in enumerate(list(previous_interactions)[-3:]):
        prompt_messages.append(LlmMessage.user(i.prompt))
        if i.is_rich_response():
            prompt_messages.append(LlmMessage.assistant("Here is some audio you previously created.  I think it's a good match"))
        else:
            prompt_messages.append(LlmMessage.assistant(i.response))
    prompt_messages.append(LlmMessage.user(prompt_text))
    llm_response = prompt_engineered_utils.get_engineered_chat_response(
        prompt_messages,
        '''
{
    "is_responding": 0|1, 
    "intent_idx": 0|1|2|3|..., 
    "content_type_idx": -1|0|1|2..., 
    "count": -1|2..., 
    "system_command": null | {
        "command_type": <command type>, 
        "command_value": <command value>
    }
}
        '''
    )
    resp = llm_response.json()
    interaction.add_feature("price_parse", llm_response.price_total)
    interaction.add_feature("millis_parse", llm_response.chat_millis)
    interaction.add_feature("tokens_parse", llm_response.token_count)
    return resp


def parse_system_command(resp):
    system_command_type = common.get(resp, ["system_command", "command_type"])
    if system_command_type:
        system_command_value = common.get(resp, ["system_command", "command_value"])
        system_command_tuples = [(system_command_type, system_command_value)]
    else:
        system_command_tuples = None
    return system_command_tuples


def parse_continue_conversation(resp, prompt_text, previous_interactions):
    if allow_conversation_continuation(previous_interactions):
        if language_utils.word_count(common.default_str(prompt_text).lower()) < 2:
            continue_conversation = True
        elif language_utils.contains_demonstrative_pronouns(common.default_str(prompt_text).lower()):
            continue_conversation = True
        else:
            continue_conversation = common.parse_bool(common.get(resp, "is_responding", "0"))
    else:
        continue_conversation = False
    return continue_conversation


def parse_intent(resp, person, intent_options) -> PromptIntent:
    intent_idx = int(common.get(resp, "intent_idx", -1))

    if intent_idx >= 0:
        intent = intent_options[intent_idx]
    else:
        intent = None

    return intent


def get_count_user_initiated_interactions(previous_interactions):
    count_user_initiated_interactions = previous_interactions.filter(
        prompt__isnull=False
    ).count()

    return count_user_initiated_interactions


def allow_conversation_continuation(previous_interactions):
    if not common.parse_bool(RuntimeConfig.instance().get("CHAT_ALLOW_CONVERSATION_CONTINUATION", default="False")):
        return False

    if len(previous_interactions) == 0:
        return False

    previous_interaction: ProjectInteraction = previous_interactions.last()

    return previous_interaction.feedback


def select_chat_channel(
        project: Project,
        person: Person,
        prompt: Prompt,
        previous_interactions: QuerySet[ProjectInteraction]
) -> Type[BaseChatChannel]:
    person = models.Person.objects.get(id=prompt.person_id)

    return LlmChatChannel


def random_select_channel(channel_message_type_options: List[Type[BaseChatChannel]]) -> Optional[Type[BaseChatChannel]]:
    # TODO - lots of opportunity for personalization / optimization / ML stuff in choosing the
    # appropriate chat channel

    disabled_channels = set(common.safe_split(RuntimeConfig.instance().get("CHAT_DISABLED_CHANNELS")))
    filtered_channels = [
        channel_cls
        for channel_cls in common.ensure_list(channel_message_type_options)
        if channel_cls.__name__ not in disabled_channels
    ]

    if filtered_channels:
        return random.choice(filtered_channels)
    else:
        return None


def is_system_channel(channel: str):
    return channel in [
        ProjectInteraction.SYSTEM_PROJECT
    ]
