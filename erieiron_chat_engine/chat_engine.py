import logging
from typing import Optional

from django.db import transaction

from erieiron_config import settings
from erieiron_chat_engine import prompt_parser
from erieiron_common import models, common
from erieiron_common.chat_utils import notify_client_of_update
from erieiron_common.enums import PubSubMessageType, PubSubMessagePriority
from erieiron_common.message_queue.pubsub_manager import PubSubManager, pubsub_workflow
from erieiron_common.models import PubSubMessage

MESSAGETYPES_GPU_ONLY = [
]


@pubsub_workflow
def initialize_workflow(pubsub_manager: PubSubManager):
    pubsub_manager.on(
        PubSubMessageType.CHAT_INTERACTION_INITIATED,
        interpret_prompt,
        on_chat_channel_error
    ).on(
        [mt for mt in prompt_parser.MESSAGE_TO_CHAT_CHANNEL_CLS.keys()],
        on_chat_work_requested,
        on_chat_channel_error
    )


def interact(
        person: models.Person,
        project: models.Project,
        prompt_text: str = None,
        playhead_millis: int = 0,
        force_recommendation: bool = False
) -> models.ProjectInteraction:
    interaction = models.ProjectInteraction.create(
        person=person,
        project=project,
        prompt=prompt_text,
        channel=models.ProjectInteraction.INTERACTION_PLACEHOLDER,
        response=None
    )

    payload = {
        "interaction_id": interaction.id,
        "playhead_millis": playhead_millis,
        "force_recommendation": force_recommendation
    }

    if settings.DEBUG:
        try:
            interpret_prompt(payload, None)
        except Exception as e:
            logging.exception(e)
            on_chat_channel_error(payload, None, e)
    else:
        PubSubManager.get_instance().publish(
            PubSubMessageType.CHAT_INTERACTION_INITIATED,
            interaction.id,
            payload,
            PubSubMessagePriority.HIGH
        )

    interaction.refresh_from_db()
    return interaction


# inspect what the user said and figure out what channel is best to handle it
def interpret_prompt(payload, prompt_parse_msg: Optional[PubSubMessage]):
    interaction = models.ProjectInteraction.objects.get(id=payload['interaction_id'])

    with transaction.atomic():
        interaction.add_feature(
            "parse msg id", common.id_or(prompt_parse_msg, "none - processed in the response thread")
        )

    project = interaction.project
    person = interaction.person
    previous_interactions = interaction.get_previous_interactions()

    prompt = prompt_parser.parse_prompt(
        person,
        previous_interactions,
        interaction
    )

    channel_cls = prompt_parser.select_chat_channel(
        project,
        person,
        prompt,
        previous_interactions
    )

    with transaction.atomic():
        models.ProjectInteraction.objects.filter(id=interaction.id).update(
            parsed_prompt=prompt.serialize(),
            channel=common.serialize_class(channel_cls),
            intent=prompt.intent
        )

    message_type = prompt_parser.CHAT_CHANNEL_CLS_TO_MESSAGE[channel_cls]
    if message_type in MESSAGETYPES_GPU_ONLY:
        # send message out to actually build the chat response
        pubsub_msg = PubSubManager.get_instance().publish(
            message_type,
            interaction.id,
            {
                "interaction_id": interaction.id
            },
            PubSubMessagePriority.HIGH
        )

        with transaction.atomic():
            interaction.add_feature(
                "process msg type", message_type
            ).add_feature(
                "process msg id", common.id_or(pubsub_msg.id, "none - weird, should have a message")
            )

    else:
        with transaction.atomic():
            interaction.add_feature(
                "process msg id", common.id_or(prompt_parse_msg, "none - processed in the response thread")
            )

        execute_chat_response_work(interaction.id)


def on_chat_work_requested(payload):
    execute_chat_response_work(payload['interaction_id'])


def execute_chat_response_work(interaction_id):
    interaction = models.ProjectInteraction.objects.get(id=interaction_id)
    channel_cls = common.deserialize_class(interaction.channel)

    response, interaction_features, associated_phrase_ids, context_maker = channel_cls(
        interaction_id
    ).build_chat_response()

    if not response:
        raise ValueError("no response generated")

    with transaction.atomic():
        models.ProjectInteraction.objects.filter(id=interaction_id).update(
            context_maker=common.serialize_class(context_maker.__class__),
            response=response
        )

        for f in common.ensure_list(interaction_features):
            interaction.add_feature(name=f[0], value=f[1])

    notify_client_of_update(interaction_id)


def on_chat_channel_error(payload, _, err):
    interaction_id = payload['interaction_id']
    interaction = models.ProjectInteraction.objects.get(id=interaction_id)

    if settings.DEBUG:
        with transaction.atomic():
            models.ProjectInteraction.objects.filter(id=interaction_id).update(
                response=f"""Error generating response: {err}
                
{common.get_stack_trace_as_string(err)}

you are seeing this error message because settings.DEBUG=True in your .env.  Production systems will not show this error
"""
            )

        notify_client_of_update(interaction_id, success=False)

    else:
        # on error return something we know will give a response (unless chatgpt is down)
        from erieiron_chat_engine.chat_channels.llm_chat_channel import LlmChatChannel

        with transaction.atomic():
            models.ProjectInteraction.objects.filter(id=interaction_id).update(
                channel=common.serialize_class(LlmChatChannel)
            )

        execute_chat_response_work(interaction_id)
