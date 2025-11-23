from functools import lru_cache
from pathlib import Path
from typing import List

import google.generativeai as genai

from erieiron_common.enums import LlmModel, LlmReasoningEffort, LlmVerbosity, LlmCreativity


@lru_cache
def get_api_key():
    from erieiron_common import aws_utils
    return aws_utils.get_secret("LLM_API_KEYS")['GEMINI']


def chat(
        all_messages: List[dict],
        model: LlmModel = LlmModel.GEMINI_3_0_PRO,
        code_response=False,
        reasoning_effort: LlmReasoningEffort = None,
        verbosity: LlmVerbosity = None,
        creativity: LlmCreativity = LlmCreativity.MEDIUM,
        schema_file: Path = None
):
    genai.configure(api_key=get_api_key())
    
    # 1. Extract the system prompt text (if it exists)
    system_instruction = next(
        ("\n".join(m.get("parts")) for m in all_messages if m.get("role") == "system"),
        None
    )
    
    # 2. Create the payload list, EXCLUDING the system message
    # (Gemini will throw an error if you include a system role in the history)
    chat_history = [
        m for m in all_messages if m.get("role") != "system"
    ]
    
    # 3. Initialize the model with the system instruction
    model_instance = genai.GenerativeModel(
        model_name=model.value,
        system_instruction=system_instruction  # <--- Passed here
    )
    
    return model_instance.generate_content(
        chat_history
    ).text
