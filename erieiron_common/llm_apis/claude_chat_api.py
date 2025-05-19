from functools import lru_cache
from typing import List

import anthropic

from erieiron_common.enums import LlmModel


@lru_cache
def get_api_key():
    # Assuming erieiron_common.aws_utils.get_secret returns a dictionary
    from erieiron_common import aws_utils
    return aws_utils.get_secret("CLAUDE_API_KEY")['CLAUDE_API_KEY']


def chat(
        messages: List[dict],
        model: LlmModel = LlmModel.CLAUDE_3_7,
        code_response=False
):
    return anthropic.Anthropic(
        api_key=get_api_key()
    ).messages.create(
        model=model.value,
        max_tokens=4000,
        messages=messages
    ).content[0].text
