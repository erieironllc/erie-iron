from functools import lru_cache
from pathlib import Path
from typing import List

import google.generativeai as genai

from erieiron_common.enums import LlmModel, LlmReasoningEffort, LlmVerbosity


@lru_cache
def get_api_key():
    from erieiron_common import aws_utils
    return aws_utils.get_secret("LLM_API_KEYS")['GEMINI']


def chat(
        messages: List[dict],
        model: LlmModel = LlmModel.GEMINI_2_5_PRO,
        code_response=False,
        reasoning_effort: LlmReasoningEffort = None,
        verbosity: LlmVerbosity = None,
        schema_file: Path = None
):
    genai.configure(api_key=get_api_key())

    return genai.GenerativeModel(
        model.value
    ).generate_content(
        messages
    ).text
