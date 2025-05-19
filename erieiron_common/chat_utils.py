import json

from erieiron_common import models, client_message_publisher
from erieiron_common.json_encoder import ErieIronJSONEncoder
from erieiron_common.enums import ClientMessage


class BaseChatResponse:
    def __init__(self, text_response):
        self.text_response = text_response
        self.response_features = []

    def add_response_feature(self, name: str, val: str):
        self.response_features.append((name, val))


class BaseRichChannelResponse(BaseChatResponse):
    def __init__(self, text_response: str, json_content: dict):
        super().__init__(text_response)
        self.json_content = json_content

    def get_response_jsons(self):
        return json.dumps(self.get_response_json(), cls=ErieIronJSONEncoder)

    def get_response_json(self):
        return {
            "source": self.__class__.__name__,
            "text_response": self.text_response,
            "json_content": self.json_content
        }


def notify_client_of_update(interaction_id, success=True, err_msg=None):
    interaction = models.ProjectInteraction.objects.get(id=interaction_id)
    person = interaction.person

    client_message_publisher.publish(
        person,
        ClientMessage.INTERACTION_RESPONSE_UPDATED,
        {
            "success": success,
            "err_msg": err_msg,
            "interaction_id": interaction.id,
            "prompt": interaction.prompt,
            "context_maker": interaction.context_maker,
            "response": interaction.response,
        }
    )
