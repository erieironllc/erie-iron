from functools import lru_cache
from pathlib import Path
from typing import List

import requests

from erieiron_common.enums import LlmModel, LlmReasoningEffort, LlmVerbosity, LlmCreativity


CREATIVITY_TO_DEEPSEEK = {
    LlmCreativity.NONE:   0.0,
    LlmCreativity.LOW:    0.2,
    LlmCreativity.MEDIUM: 0.5,
    LlmCreativity.HIGH:   0.9,
}


@lru_cache
def get_api_key():
    from erieiron_common import aws_utils
    return aws_utils.get_secret("LLM_API_KEYS")['DEEPSEEK']


def chat(
        messages: List[dict],
        model: LlmModel = LlmModel.DEEPSEEK_CHAT,
        code_response=False,
        reasoning_effort: LlmReasoningEffort = None,
        verbosity: LlmVerbosity = None,
        creativity: LlmCreativity = LlmCreativity.MEDIUM,
        schema_file: Path = None
):
    temperature = CREATIVITY_TO_DEEPSEEK.get(creativity, 0.5)
    response = requests.post(
        "https://api.deepseek.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {get_api_key()}",
            "Content-Type": "application/json"
        },
        json={
            "model": model.value,
            "messages": messages,
            "max_tokens": 1024,
            "temperature": temperature
        }
    )

    if response.status_code != 200:
        raise Exception(f"API request failed with status code {response.status_code}: {response.text}")

    return response.json()["choices"][0]["message"]["content"]
