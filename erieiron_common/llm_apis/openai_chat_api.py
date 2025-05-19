import logging
from functools import lru_cache
from typing import List

import openai

from erieiron_common.enums import LlmModel

logging.getLogger('openai').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)


@lru_cache
def get_api_key():
    from erieiron_common import aws_utils
    return aws_utils.get_secret("OPENAI_API_KEY")['OPENAI_API_KEY']


def chat(
        messages: List[dict],
        model=LlmModel.OPENAI_GPT_O3_MINI,
        code_response=False
):
    return openai.OpenAI(
        api_key=get_api_key()
    ).chat.completions.create(
        model=model.value,
        messages=messages
    ).choices[0].message.content
