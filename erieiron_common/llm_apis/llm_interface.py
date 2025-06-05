import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Tuple

import tiktoken

from erieiron_common import common
from erieiron_common.enums import LlmModel, LlmMessageType
from erieiron_common.llm_apis import gemini_chat_api, openai_chat_api, claude_chat_api, deepseek_chat_api

# want these to be cheap and fast- only need to parse a simple prompt and return json
PARSE_MODELS_IN_ORDER = [
    LlmModel.OPENAI_GPT_4_1_NANO,
    LlmModel.GEMINI_2_0_FLASH,
    LlmModel.DEEPSEEK_CODER
]

CHAT_MODELS_IN_ORDER = [
    LlmModel.OPENAI_GPT_4o,
    LlmModel.OPENAI_GPT_4_1_MINI,
    LlmModel.GEMINI_2_5_PRO,
    LlmModel.OPENAI_GPT_O3_MINI,
    LlmModel.CLAUDE_3_7,
    LlmModel.DEEPSEEK_CHAT,
]

CODE_MODELS_IN_ORDER = [
    LlmModel.GEMINI_2_5_PRO,
    # LlmModel.OPENAI_GPT_4o,
    # LlmModel.DEEPSEEK_CODER,
    # LlmModel.CLAUDE_3_7 # context window too small
]

MODEL_TO_IMPL = {
    LlmModel.OPENAI_GPT_O3_MINI: openai_chat_api,
    LlmModel.OPENAI_GPT_4o: openai_chat_api,
    LlmModel.OPENAI_GPT_4o_20240806: openai_chat_api,
    LlmModel.OPENAI_GPT_4_TURBO: openai_chat_api,
    LlmModel.OPENAI_GPT_45_DO_NOT_USE_VERY_VERY_EXPENSIVE: openai_chat_api,
    LlmModel.OPENAI_GPT_3_5_TURBO: openai_chat_api,
    LlmModel.OPENAI_GPT_4_1: openai_chat_api,
    LlmModel.OPENAI_GPT_4_1_MINI: openai_chat_api,
    LlmModel.OPENAI_GPT_4_1_NANO: openai_chat_api,
    LlmModel.OPENAI_GPT_4_5: openai_chat_api,
    LlmModel.OPENAI_O1: openai_chat_api,
    LlmModel.OPENAI_O1_MINI: openai_chat_api,
    LlmModel.OPENAI_O3: openai_chat_api,
    LlmModel.OPENAI_O4: openai_chat_api,
    LlmModel.OPENAI_O4_MINI: openai_chat_api,

    LlmModel.GEMINI_2_5_PRO: gemini_chat_api,
    LlmModel.GEMINI_2_0_FLASH: gemini_chat_api,

    LlmModel.CLAUDE_3_7: claude_chat_api,
    LlmModel.CLAUDE_3_5: claude_chat_api,

    LlmModel.DEEPSEEK_CODER: deepseek_chat_api,
    LlmModel.DEEPSEEK_CHAT: deepseek_chat_api
}

MODEL_TO_MAX_TOKENS = {
    LlmModel.OPENAI_GPT_O3_MINI: 200_000,
    LlmModel.OPENAI_GPT_4o: 128_000,
    LlmModel.OPENAI_GPT_4o_20240806: 128_000,
    LlmModel.OPENAI_GPT_4_TURBO: 128_000,
    LlmModel.OPENAI_GPT_45_DO_NOT_USE_VERY_VERY_EXPENSIVE: 128_000,
    LlmModel.OPENAI_GPT_3_5_TURBO: 4_096,
    LlmModel.OPENAI_GPT_4_1: 1_000_000,
    LlmModel.OPENAI_GPT_4_1_MINI: 1_000_000,
    LlmModel.OPENAI_GPT_4_1_NANO: 1_000_000,
    LlmModel.OPENAI_GPT_4_5: 128_000,
    LlmModel.OPENAI_O1: 128_000,
    LlmModel.OPENAI_O1_MINI: 128_000,
    LlmModel.OPENAI_O3: 200_000,
    LlmModel.OPENAI_O4: 1_000_000,
    LlmModel.OPENAI_O4_MINI: 1_000_000,

    LlmModel.GEMINI_2_5_PRO: 200_000,
    LlmModel.GEMINI_2_0_FLASH: 200_000,

    LlmModel.CLAUDE_3_7: 20_000,  # should be 128k, but they rate limit us
    LlmModel.CLAUDE_3_5: 40_000,
    LlmModel.CLAUDE_3_OPUS_DO_NOT_USE_VERY_EXPENSIVE: 128_000,

    LlmModel.DEEPSEEK_CODER: 65536,
    LlmModel.DEEPSEEK_CHAT: 65536
}

MODEL_PRICE_USD_PER_MILLION_TOKENS = {
    LlmModel.OPENAI_GPT_45_DO_NOT_USE_VERY_VERY_EXPENSIVE: {
        "input": 75.00,
        "output": 150.00,
    },
    LlmModel.CLAUDE_3_OPUS_DO_NOT_USE_VERY_EXPENSIVE: {
        "input": 15.00,
        "output": 75.00,
    },
    LlmModel.OPENAI_GPT_O3_MINI: {
        "input": 1.10,
        "output": 4.40,
    },
    LlmModel.OPENAI_GPT_4o: {
        "input": 5.00,
        "output": 15.00,
    },
    LlmModel.OPENAI_GPT_4o_20240806: {
        "input": 5.00,
        "output": 15.00,
    },
    LlmModel.OPENAI_GPT_4_TURBO: {
        "input": 3.00,
        "output": 6.00,
    },
    LlmModel.OPENAI_GPT_3_5_TURBO: {
        "input": 2.00,
        "output": 2.00,
    },
    LlmModel.OPENAI_GPT_4_1: {
        "input": 2.50,
        "output": 10.00,
    },
    LlmModel.OPENAI_GPT_4_1_MINI: {
        "input": 0.15,
        "output": 0.60,
    },
    LlmModel.OPENAI_GPT_4_1_NANO: {
        "input": 0.05,
        "output": 0.20,
    },
    LlmModel.OPENAI_GPT_4_5: {
        "input": 75.00,
        "output": 150.00,
    },
    LlmModel.OPENAI_O1: {
        "input": 15.00,
        "output": 60.00,
    },
    LlmModel.OPENAI_O1_MINI: {
        "input": 3.00,
        "output": 12.00,
    },
    LlmModel.OPENAI_O3: {
        "input": 10.00,
        "output": 40.00,
    },
    LlmModel.OPENAI_O4: {
        "input": 20.00,
        "output": 80.00,
    },
    LlmModel.OPENAI_O4_MINI: {
        "input": 5.00,
        "output": 20.00,
    },
    LlmModel.GEMINI_2_5_PRO: {
        "input": 1.25,
        "output": 10
    },
    LlmModel.GEMINI_2_0_FLASH: {
        "input": 0.10,
        "output": 0.40,
    },
    LlmModel.CLAUDE_3_7: {
        "input": 3.00,
        "output": 15.00,
    },
    LlmModel.CLAUDE_3_5: {
        "input": 3.00,
        "output": 15.00,
    },
    LlmModel.DEEPSEEK_CODER: {
        "input": 0.14,
        "output": 0.28,
    },
    LlmModel.DEEPSEEK_CHAT: {
        "input": 0.27,
        "output": 1.10,
    }
}


def chat(prompt, model: LlmModel = None, code_response=False) -> 'LlmResponse':
    if model is None:
        models = CODE_MODELS_IN_ORDER if code_response else CHAT_MODELS_IN_ORDER
    else:
        models = [model]

    for idx, model in enumerate(models):
        model = LlmModel(model)

        try:
            impl = MODEL_TO_IMPL[LlmModel(model)]

            messages = LlmMessage.parse_prompt(model, prompt, code_response)
            json_messages = [m.get_message_json(model) for m in messages]

            start_time = time.time()
            resp = impl.chat(
                json_messages,
                model,
                code_response
            )
            chat_time = (time.time() - start_time) * 1000
            token_count = LlmMessage.get_total_token_count(model, messages)
            logging.info(f"chat with {model.value} took {chat_time:.2f}ms for {token_count} tokens")

            response_text = post_process_response(resp)

            price_total, price_input, price_output = LlmMessage.get_price(
                model,
                messages,
                response_text
            )

            return LlmResponse(
                text=response_text,
                model=model,
                price_total=price_total,
                price_input=price_input,
                price_output=price_output,
                token_count=token_count,
                chat_millis=chat_time
            )
        except Exception as e:
            is_last = idx == len(models) - 1
            if is_last:
                raise e
            else:
                logging.exception(f"chat with {model} failed.  will try {models[idx + 1]}")


def post_process_response(resp):
    resp = resp.strip()
    if resp.startswith("```markdown"):
        resp = resp[len("```markdown"):]
    if resp.startswith("```json"):
        resp = resp[len("```json"):]
    if resp.startswith("```python"):
        resp = resp[len("```python"):]
    if resp.endswith("```"):
        resp = resp[:-len("```")]
    return resp


def sanitize_prompt(raw_text: str) -> str:
    return raw_text

    # pii_entities = analyzer.analyze(text=raw_text, language="en")
    # operator_config = {
    #     "DEFAULT": OperatorConfig(
    #         "mask",
    #         {
    #             "masking_char": "*",
    #             "chars_to_mask": 0,
    #             "from_end": False,
    #         },
    #     )
    # }

    # sanitized = anonymizer.anonymize(
    #     text=raw_text,
    #     analyzer_results=pii_entities,
    #     operators=operator_config,
    # )

    # return sanitized.text


@dataclass
class LlmResponse:
    text: str
    model: LlmModel
    price_total: float
    price_input: float
    price_output: float
    token_count: int
    chat_millis: float
    parsed_json: Optional = None

    def json(self):
        if not self.parsed_json:
            self.parsed_json = ensure_parsable_json(self.text)

        return self.parsed_json


class LlmMessage:
    def __init__(self, message_type: LlmMessageType, text: str, file: Path = None):
        self.message_type: LlmMessageType = message_type
        self.file = file

        if text and file:
            self.text = ""
            for line in text.split("\n"):
                self.text += f"{common.comment_out_line(file, line)}\n"

            self.text += common.comment_out_line(file, f"=============== start {file} contents ================")
            self.text += "\n"
            self.text += Path(file).read_text()
            self.text += "\n"
            self.text += common.comment_out_line(file, f"=============== end {file} contents ================")
        elif text:
            self.text = text
        elif file:
            self.text = Path(file).read_text()
        else:
            raise Exception("inconceivable")

    def __str__(self):
        return (f"""-----------------------
{self.message_type.label()}  Message:
{self.text.strip()}
-----------------------""")

    @staticmethod
    def get_price(model: LlmModel, input_messages: List['LlmMessage'], response_text: str) -> Tuple[float, float, float]:
        usd_per_million_input_token = MODEL_PRICE_USD_PER_MILLION_TOKENS[model]['input']
        usd_per_million_output_token = MODEL_PRICE_USD_PER_MILLION_TOKENS[model]['output']

        price_input = LlmMessage.get_total_token_count(model, input_messages) * usd_per_million_input_token / 1000000
        price_output = LlmMessage._get_token_count(model, response_text) / 1000000
        return price_input + price_output, price_input, price_output

    def get_message_json(self, model: LlmModel) -> dict:
        model = LlmModel(model)

        role_str = self.message_type.value
        if model in [LlmModel.GEMINI_2_5_PRO, LlmModel.GEMINI_2_0_FLASH]:
            if LlmMessageType.SYSTEM.eq(self.message_type):
                role_str = "user"
            elif LlmMessageType.ASSISTANT.eq(self.message_type):
                role_str = "model"
        elif model in [LlmModel.CLAUDE_3_7, LlmModel.CLAUDE_3_5]:
            if LlmMessageType.SYSTEM.eq(self.message_type):
                role_str = "user"

        sanitized_text = sanitize_prompt(self.text)
        if model in [LlmModel.GEMINI_2_5_PRO, LlmModel.GEMINI_2_0_FLASH]:
            d = {
                "role": role_str,
                "parts": [sanitized_text]
            }
        else:
            d = {
                "role": role_str,
                "content": sanitized_text
            }

        return d

    @staticmethod
    def get_total_token_count(model: LlmModel, messages: List['LlmMessage']) -> int:
        return sum([m.get_token_count(model) for m in messages]) + (4 * len(messages))

    @staticmethod
    def _get_token_count(model: LlmModel, s: str) -> int:
        try:
            encoding = tiktoken.encoding_for_model(model.value)
        except KeyError:
            encoding = tiktoken.get_encoding("cl100k_base")

        return len(encoding.encode(s))

    def get_token_count(self, model: LlmModel) -> int:
        return LlmMessage._get_token_count(
            model,
            json.dumps(self.get_message_json(model))
        )

    @staticmethod
    def parse_prompt(model, prompt, code_response=False) -> List['LlmMessage']:
        messages = []

        if code_response:
            messages.append(
                LlmMessage(
                    message_type=LlmMessageType.SYSTEM,
                    text="""
you are an expert code generation assistant. 
respond only with valid code. do not include any markdown formatting, such as triple backticks or language tags.
if responding with json, the property names must be encosed in "double quotes"
                    """
                )
            )

        for m in common.filter_none(prompt):
            if isinstance(m, str):
                if m:
                    messages.append(LlmMessage.user(m))
            elif isinstance(m, LlmMessage):
                if m.text:
                    messages.append(m)
            else:
                raise ValueError(f"invalid message type {m}")

        token_count = LlmMessage.get_total_token_count(model, messages)
        while token_count > MODEL_TO_MAX_TOKENS.get(model, sys.maxsize):
            messages = messages[1:]
            token_count = LlmMessage.get_total_token_count(model, messages)

        return messages

    @classmethod
    def assistant(cls, txt, file=None):
        return LlmMessage(
            message_type=LlmMessageType.ASSISTANT,
            text=txt,
            file=file
        )

    @classmethod
    def user(cls, txt, file=None):
        return LlmMessage(
            message_type=LlmMessageType.USER,
            text=txt,
            file=file
        )

    @classmethod
    def sys(cls, txt, file=None):
        return LlmMessage(
            message_type=LlmMessageType.SYSTEM,
            text=txt,
            file=file
        )

    @classmethod
    def log(cls, messages: list['LlmMessage']):
        for m in messages:
            print(f"""
========= Message Type: {m.message_type.label()} ==========
{m.text}
            """)


def ensure_parsable_json(json_text: str) -> dict:
    orig_json_text = json_text
    price = 0
    last_e = None
    for i in range(5):
        if not json_text:
            raise Exception(f"json_text is empty")

        while len(json_text) > 0 and json_text[0] != "{":
            json_text = json_text[1:]

        while len(json_text) > 0 and json_text[-1] != "}":
            json_text = json_text[:-1]

        if common.is_empty(json_text):
            raise Exception(f"unable to parse json\n{orig_json_text}")

        try:
            parsed_text = json.loads(json_text)
            return parsed_text
        except Exception as e:
            print(f"----------\n{json_text}\n\n{e}\n--------------")

            last_e = e
            llm_response_reformat = chat(
                f"""
please format and return the following json text as valid and parsable json:

========= json text start ================
{json_text}
========= json text end ================


the previous attempt at parsing this content resulted in this error:  {e}


resond only with parsable json.  do not include any comments, explanations, or non-json markdown
""",
                LlmModel.OPENAI_GPT_O3_MINI,
                code_response=True
            )
            json_text = llm_response_reformat.text

    raise last_e
