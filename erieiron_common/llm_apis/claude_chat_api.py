from functools import lru_cache
from pathlib import Path
from typing import List

import anthropic

from erieiron_common.enums import LlmModel, LlmReasoningEffort, LlmVerbosity, LlmCreativity


# Mapping from LlmCreativity to Anthropic sampling parameters
CREATIVITY_TO_ANTHROPIC = {
    LlmCreativity.NONE:   {"temperature": 0.0, "top_p": None, "top_k": 1},
    LlmCreativity.LOW:    {"temperature": None, "top_p": 0.9, "top_k": 20},
    LlmCreativity.MEDIUM: {"temperature": None, "top_p": 0.9, "top_k": 40},
    LlmCreativity.HIGH:   {"temperature": 0.9, "top_p": None, "top_k": 100},
}


@lru_cache
def get_api_key():
    # Assuming erieiron_common.aws_utils.get_secret returns a dictionary
    from erieiron_common import aws_utils
    return aws_utils.get_secret("LLM_API_KEYS")['ANTHROPIC']


def chat(
        messages: List[dict],
        model: LlmModel = LlmModel.CLAUDE_4_5,
        code_response=False,
        reasoning_effort: LlmReasoningEffort = None,
        verbosity: LlmVerbosity = None,
        creativity: LlmCreativity = LlmCreativity.MEDIUM,
        schema_file: Path = None
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

    # Select sampling parameters based on creativity
    sampling = CREATIVITY_TO_ANTHROPIC.get(creativity, CREATIVITY_TO_ANTHROPIC[LlmCreativity.MEDIUM])

    temperature = sampling["temperature"]
    top_p = sampling["top_p"]
    top_k = sampling["top_k"]

    stream_kwargs = {
        "model": model.value,
        "max_tokens": 4000,
        "messages": messages_not_sys,
        "system": "\n\n".join(messages_sys),
        "extra_headers": extra_headers,
        "top_k": top_k,
    }

    if temperature is not None:
        stream_kwargs["temperature"] = temperature
    elif top_p is not None:
        stream_kwargs["top_p"] = top_p

    with client.messages.stream(**stream_kwargs) as stream:
        response_text = ""
        for text in stream.text_stream:
            response_text += text
        return response_text
