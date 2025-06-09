from pathlib import Path

from erieiron_common.enums import PubSubMessageType
from erieiron_common.message_queue.pubsub_manager import PubSubManager


def submit_business_idea(idea_content: str, existing_business_id=None):
    if isinstance(idea_content, Path):
        idea_content = idea_content.read_text()

    PubSubManager.publish(
        PubSubMessageType.BUSINESS_IDEA_SUBMITTED,
        payload={
            "business_id": existing_business_id,
            "idea_content": idea_content
        }
    )
