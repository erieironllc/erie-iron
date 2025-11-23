import logging
import pprint
import re
import time
from functools import lru_cache
from typing import List

import openai
from openai.types.responses import ResponseUsage

from erieiron_common import common
from erieiron_common.enums import LlmModel, LlmVerbosity, LlmReasoningEffort, LlmCreativity
from erieiron_common.llm_apis.llm_response import LlmResponse

logging.getLogger('openai').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)

CREATIVITY_TO_SAMPLING = {
    LlmCreativity.NONE: {
        "temperature": 0.0,
        "top_p": 1.0,
    },
    LlmCreativity.LOW: {
        "temperature": 0.2,
        "top_p": 0.9,
    },
    LlmCreativity.MEDIUM: {
        "temperature": 0.5,
        "top_p": 0.9,
    },
    LlmCreativity.HIGH: {
        "temperature": 0.9,
        "top_p": 0.95,
    },
}


REASONING_TO_VAL = {
    LlmReasoningEffort.NONE: "none",
    LlmReasoningEffort.MINIMAL: "low",
    LlmReasoningEffort.LOW: "low",
    LlmReasoningEffort.MEDIUM: "medium",
    LlmReasoningEffort.HIGH: "high"
}



@lru_cache
def get_api_key():
    from erieiron_common import aws_utils
    return aws_utils.get_secret("LLM_API_KEYS")['OPENAI']


def chat(
        messages: List[dict],
        model=LlmModel.OPENAI_GPT_4o,
        code_response=False,
        reasoning_effort: LlmReasoningEffort = None,
        verbosity: LlmVerbosity = None,
        creativity: LlmCreativity = LlmCreativity.MEDIUM

):
    reasoning_effort = REASONING_TO_VAL.get(LlmReasoningEffort(reasoning_effort))
    start_time = time.time()
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
        kwargs = {
            "model": model_name,
            "input": messages,
        }
        supports_reasoning = model_name.startswith(("gpt-5", "gpt-4o", "o"))
        if reasoning_effort and supports_reasoning:
            kwargs["reasoning"] = {"effort": reasoning_effort}
        
        # Determine if this model supports sampling parameters (temperature, top_p)
        sampling = CREATIVITY_TO_SAMPLING.get(creativity, CREATIVITY_TO_SAMPLING[LlmCreativity.MEDIUM])
        supports_sampling = False #open ai does not yet support sampling
        if supports_sampling:
            kwargs["temperature"] = sampling["temperature"]
            kwargs["top_p"] = sampling["top_p"]
        
        supports_verbosity = model_name.startswith(("gpt-5", "gpt-4o"))
        if verbosity and supports_verbosity:
            kwargs["text"] = {"verbosity": verbosity.value}
        
        resp = client.responses.create(**kwargs)
        chat_time = (time.time() - start_time) * 1000
        
        usage: ResponseUsage = getattr(resp, 'usage', {})
        return LlmResponse(
            text=common.default_str(resp.output_text).strip(),
            model=model,
            input_token_count=usage.input_tokens,
            output_token_count=usage.output_tokens,  # this includes 'reasoning' tokens
            chat_millis=chat_time
        )
    
    # Determine endpoint
    # is_chat_candidate = model_name.startswith(("gpt-", "gpt4", "o4"))
    # is_response_candidate = model_name.startswith("o3")
    # else fallback to completions
    
    try:
        # responses is all we need now
        return call_responses()
        
        # if is_chat_candidate:
        #     return call_chat()
        # elif is_response_candidate:
        #     return call_responses()
        # else:
        #     return call_completions()
    except Exception as e:
        logging.exception(e)
        err = str(e)
        if "only supported in v1/responses" in err:
            return call_responses()
        if "not supported in the v1/chat/completions endpoint" in err:
            return call_chat()
        if "not supported in the v1/completions endpoint" in err:
            return call_completions()
        raise
