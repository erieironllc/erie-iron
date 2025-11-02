from functools import lru_cache
from pathlib import Path
from typing import List

from erieiron_common.enums import LlmReasoningEffort, LlmVerbosity


@lru_cache
def get_api_key():
    from erieiron_common import aws_utils
    return aws_utils.get_secret("OPENAI_API_KEY")['OPENAI_API_KEY']


def chat(
        messages: List[dict],
        model,
        code_response,
        reasoning_effort: LlmReasoningEffort = None,
        verbosity: LlmVerbosity = None,
        schema_file: Path = None
):
    raise NotImplementedError("LLaMA chat adapter is not implemented")
