from functools import lru_cache
from typing import List


@lru_cache
def get_api_key():
    from erieiron_common import aws_utils
    return aws_utils.get_secret("OPENAI_API_KEY")['OPENAI_API_KEY']


def chat(
        messages: List[dict],
        model,
        code_response
):
    # fireworks.api_key = get_api_key()
    # response = fireworks.chat.completions.create(
    #     model=model.value,
    #     messages=messages
    # )
    # return response.choices[0].message.content
    raise Exception("not implemented")
