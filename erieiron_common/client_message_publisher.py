from typing import Dict

from erieiron_common import common
from erieiron_common.enums import ClientMessage


def publish(person, message_type: ClientMessage, payload: Dict = None):
    common.log_debug(f"sent message {message_type.value} to {person.pk}")

    from erieiron_common import aws_utils
    aws_utils.get_aws_interface().send_client_message(
        person,
        message_type.value,
        payload
    )
