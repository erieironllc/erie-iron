from erieiron_common.enums import LlmMessageType, PubSubMessageType
from erieiron_common.llm_apis import llm_interface
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_common.message_queue.pubsub_manager import PubSubManager
import sys


def llm_chat_text_response(task_id: str, messages: list[tuple[str, str]]) -> str:
    """
    Text Chat with a LLM
    task_id:  the id of the task this chat is related to.  (used for logging and budget purposes)
    messages: list of tuples of [message_type, message_text].  message_type is one of [system | assistant | user], message_text is the message text

    returns: raw string text response from the llm
    """

    llm_messages = []
    for message_type, message_text in messages:
        llm_message_type = LlmMessageType.valid_or(message_type.lower())

        if not llm_message_type:
            raise Exception(f"{message_type} is not a valid message_type.  must be one of [system | assistant | user]")

        llm_messages.append(
            LlmMessage(llm_message_type, message_text or "")
        )

    llm_response = llm_interface.chat(llm_messages)

    if "pytest" not in sys.modules:
        PubSubManager.publish(
            PubSubMessageType.TASK_SPEND,
            task_id,
            {
                "task_id": task_id,
                "usd_spent": llm_response.price_total
            }
        )

    return llm_response.text


def llm_chat_json_response(task_id: str, messages: list[tuple[str, str]]) -> dict:
    """
    Chat with a LLM and get a JSON response
    task_id:  the id of the task this chat is related to.  (used for logging and budget purposes)
    messages: list of tuples of [message_type, message_text].  message_type is one of [system | assistant | user], message_text is the message text

    returns: json response from the llm parsed and returned as dict"""

    llm_messages = []
    for message_type, message_text in messages:
        llm_message_type = LlmMessageType.valid_or(message_type.lower())

        if not llm_message_type:
            raise Exception(f"{message_type} is not a valid message_type.  must be one of [system | assistant | user]")

        llm_messages.append(
            LlmMessage(llm_message_type, message_text or "")
        )

    llm_response = llm_interface.chat(llm_messages, code_response=True)

    if "pytest" not in sys.modules:
        PubSubManager.publish(
            PubSubMessageType.TASK_SPEND,
            task_id,
            {
                "task_id": task_id,
                "usd_spent": llm_response.price_total
            }
        )

    return llm_response.json()
