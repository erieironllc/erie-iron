import logging
from typing import Tuple

from erieiron_common import common
from erieiron_common.enums import LlmCreativity
from erieiron_common.llm_apis import llm_interface
from erieiron_common.llm_apis.llm_constants import PARSE_MODELS_IN_ORDER
from erieiron_common.llm_apis.llm_interface import LlmResponse, LlmMessage


def parse_int(question: str, fuzzy_input: str, min_max: Tuple[int, int] = None) -> int:
    fuzzy_input = common.default_str(fuzzy_input)

    response = None
    try:
        response = prompt_engineered_chat(
            [
                LlmMessage.sys("You are an expert and parsing numbers out of text"),
                LlmMessage.assistant(question),
                LlmMessage.user(f"""
What is your best guess at the integer value described by:
{fuzzy_input}
                """),

            ],
            '{"integer_value": < integer_value >}'
        )

        return common.parse_int(common.get(response, 'integer_value', 0), min_max=min_max)
    except Exception as e:
        logging.exception(e)
        common.log_info(f"value not found for fuzzy input {fuzzy_input}: {response}")
        return 0


def parse_time_to_millis(fuzzy_input: str) -> int:
    fuzzy_input = common.default_str(fuzzy_input).upper()

    response = None
    try:
        response = prompt_engineered_chat(
            [
                LlmMessage.sys("""
You are an expert at parsing time out of arbitrary text.  

You communicate the parsed time in milliseconds

When the user sends prompt text, you parse it and identify the millisends they are describing

If the user didn't specify a time unit in their prompt, then you will identify the time unit from this rubrik:
1) if the value they input is only numeric and less than 10, you will assume the user is communicating minutes
2) if the value they input is only numberic and greater than 10, you will assume the user is communicating seconds
                """),
                LlmMessage.user(fuzzy_input)
            ],
            '{"value": < milliseconds >}'
        )

        return int(common.get(response, 'value', 0))
    except Exception as e:
        logging.exception(e)
        common.log_info(f"value not found for fuzzy input {fuzzy_input}: {response}")
        return 0


def get_value_from_fuzzy_input(field_description: str, available_options: list, fuzzy_input: str):
    fuzzy_input = common.default_str(fuzzy_input).upper()
    available_options_upper = [s.upper() for s in available_options if common.is_not_empty(s)]

    if fuzzy_input in [s.upper() for s in available_options]:
        return fuzzy_input

    response = None
    try:
        response = prompt_engineered_chat(
            [
                LlmMessage.sys(f"""
You are an expert at parsing values given a text description.  You are an expert in music semantics

You are part of a system that supports the following values for {field_description}: {",".join(available_options)}.  

When the user supplies arbitrary text, you pick the "supported value" you feel best matches what they are describing
                """),
                LlmMessage.assistant(f"What is the value of {field_description}?"),
                LlmMessage.user(fuzzy_input)
            ],
            '{"value": < supported_value >}'
        )

        guessed_value = response['value'].upper()
        if guessed_value.upper() in available_options_upper:
            return guessed_value
        else:
            return None
    except Exception as e:
        logging.exception(e)
        common.log_info(f"value not found for fuzzy input {fuzzy_input}: {response}")
        return None


def prompt_engineered_chat(
        prompt: str,
        response_format: str,
        model=None
) -> dict:
    return get_engineered_chat_response(
        prompt,
        response_format,
        model
    ).json()


def get_engineered_chat_response(
        prompt,
        response_format: str,
        model=None
) -> LlmResponse:
    prompt_messages = [
        LlmMessage.sys(f"""
Answer with json in the format 
====== start json format =======
{response_format}
====== end json format =======

Respond only with the json in the above format, with no other text or markup.
""")
    ]

    for p in common.ensure_list(prompt):
        if isinstance(p, str):
            prompt_messages.append(LlmMessage.user(p))
        elif isinstance(p, LlmMessage):
            prompt_messages.append(p)
        else:
            raise ValueError(f"unhandled prompt type {p.__class__}")

    if model:
        models = [model] + PARSE_MODELS_IN_ORDER
    else:
        models = PARSE_MODELS_IN_ORDER

    last_exception = None
    for model in models:
        try:
            resp = llm_interface.chat(
                prompt_messages,
                model=model,
                code_response=True,
                creativity=LlmCreativity.LOW
            )

            # make sure it's parseable as json
            resp.json()

            return resp
        except Exception as e:
            last_exception = e
            logging.exception(e)

    raise last_exception


def trim_json_string(input_string):
    input_string = common.default_str(input_string)

    if input_string.startswith("json"):
        input_string = input_string[len("json"):]

    input_string = input_string.replace("\n", " ").strip()
    start_idx = input_string.find('{')
    end_idx = input_string.rfind('}')
    if start_idx != -1 and end_idx != -1:
        return input_string[start_idx:end_idx + 1]
    return input_string  # Return original string if { or } not found
