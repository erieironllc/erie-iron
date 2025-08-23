import json
import logging
import pprint
import re
from functools import lru_cache
from typing import List

import openai

from erieiron_common import common
from erieiron_common.enums import LlmModel

logging.getLogger('openai').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)


@lru_cache
def get_api_key():
    from erieiron_common import aws_utils
    return aws_utils.get_secret("LLM_API_KEYS")['OPENAI']


def chat(
        messages: List[dict],
        model=LlmModel.OPENAI_GPT_4o,
        code_response=False
):
    client = openai.OpenAI(api_key=get_api_key())

    # Normalize model name by removing trailing date suffix if present
    model_name = re.sub(r'-\d{4}-\d{2}-\d{2}$', '', model.value)

    def call_chat():
        try:
            return client.chat.completions.create(
                model=model_name,
                messages=messages
            ).choices[0].message.content
        except:
            pprint.pprint(messages)
            raise 

    def call_completions():
        prompt = ""
        for msg in messages:
            role = msg["role"]
            content = msg["content"].strip()
            prompt += f"[{role}]: {content}\n\n"
        prompt += "[assistant]:"
        return client.completions.create(
            model=model_name,
            prompt=prompt
        ).choices[0].text

    def call_responses():
        prompt = ""
        for msg in messages:
            role = msg["role"]
            content = msg["content"].strip()
            prompt += f"[{role}]: {content}\n\n"
        prompt += "[assistant]:"
        resp = client.responses.create(
            model=model_name,
            input=prompt
        )
        return common.default_str(resp.output_text).strip()

    # Determine endpoint
    is_chat_candidate = model_name.startswith(("gpt-", "gpt4", "o4"))
    is_response_candidate = model_name.startswith("o3")
    # else fallback to completions

    try:
        if is_chat_candidate:
            return call_chat()
        elif is_response_candidate:
            return call_responses()
        else:
            return call_completions()
    except Exception as e:
        err = str(e)
        if "only supported in v1/responses" in err:
            return call_responses()
        if "not supported in the v1/chat/completions endpoint" in err:
            return call_chat()
        if "not supported in the v1/completions endpoint" in err:
            return call_completions()
        raise
