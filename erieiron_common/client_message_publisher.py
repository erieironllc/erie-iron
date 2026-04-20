from typing import Dict

from django.conf import settings

from erieiron_common import common
from erieiron_common.enums import ClientMessage


def _client_message_websocket_enabled() -> bool:
    websocket_endpoint = common.default_str(
        settings.CLIENT_MESSAGE_WEBSOCKET_ENDPOINT
    ).strip()
    return bool(websocket_endpoint) and websocket_endpoint.lower() != "none"


def publish(person, message_type: ClientMessage, payload: Dict = None):
    if not _client_message_websocket_enabled():
        common.log_debug(
            f"skipping client message {message_type.value} to {person.pk}; websocket disabled"
        )
        return

    common.log_debug(f"sending client message {message_type.value} to {person.pk}")

    from erieiron_common import aws_utils
    aws_utils.get_aws_interface().send_client_message(
        person,
        message_type.value,
        payload
    )
