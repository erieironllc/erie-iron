from functools import lru_cache
from typing import List

import anthropic

from erieiron_common.enums import LlmModel


@lru_cache
def get_api_key():
    # Assuming erieiron_common.aws_utils.get_secret returns a dictionary
    from erieiron_common import aws_utils
    return aws_utils.get_secret("LLM_API_KEYS")['ANTHROPIC']


def chat(
        messages: List[dict],
        model: LlmModel = LlmModel.CLAUDE_3_7,
        code_response=False
):
    client = anthropic.Anthropic(api_key=get_api_key())

    extra_headers = {"anthropic-beta": "output-128k-2025-02-19"}
    
    # Anthropic API expects system message as a separate argument, not inside the messages list
    messages_sys = []
    messages_not_sys = []
    for m in messages:
        if m['role'] == "system":
            messages_sys.append(m['content'])
        else:
            messages_not_sys.append(m)

    with client.messages.stream(
        model=model.value,
        max_tokens=4000, #128_000,
        messages=messages_not_sys,
        system="\n\n".join(messages_sys),
        extra_headers=extra_headers
    ) as stream:
        response_text = ""
        for text in stream.text_stream:
            response_text += text
        return response_text
