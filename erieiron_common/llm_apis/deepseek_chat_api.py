from functools import lru_cache
from typing import List

import requests

from erieiron_common.enums import LlmModel


@lru_cache
def get_api_key():
    from erieiron_common import aws_utils
    return aws_utils.get_secret("LLM_API_KEYS")['DEEPSEEK']


def chat(
        messages: List[dict],
        model: LlmModel = LlmModel.DEEPSEEK_CHAT,
        code_response=False
):
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
            "temperature": 0.2
        }
    )

    if response.status_code != 200:
        raise Exception(f"API request failed with status code {response.status_code}: {response.text}")

    return response.json()["choices"][0]["message"]["content"]
