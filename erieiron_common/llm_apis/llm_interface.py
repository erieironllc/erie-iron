import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Tuple

import tiktoken
from jsonschema import validate as jsonschema_validate

from erieiron_common import common
from erieiron_common.enums import LlmModel, LlmMessageType
from erieiron_common.json_encoder import ErieIronJSONEncoder
from erieiron_common.llm_apis.llm_constants import CODE_PLANNING_MODELS_IN_ORDER, CHAT_MODELS_IN_ORDER, MODEL_TO_IMPL, MODEL_PRICE_USD_PER_MILLION_TOKENS, MODEL_TO_MAX_TOKENS


def chat(
        messages: list['LlmMessage'],
        model: LlmModel = None,
        output_schema: Path = None,
        code_response=False,
        debug=False
) -> 'LlmResponse':
    messages = common.ensure_list(messages)
    
    if output_schema and output_schema.exists():
        code_response = True
        messages.append(LlmMessage.sys("The output json will be validated against this schema", output_schema))
        
    if not model:
        models = CODE_PLANNING_MODELS_IN_ORDER if code_response else CHAT_MODELS_IN_ORDER
    else:
        models = common.ensure_list(model)

    for idx, model in enumerate(models):
        model = LlmModel(model)

        try:
            impl = MODEL_TO_IMPL[LlmModel(model)]

            messages = LlmMessage.parse_prompt(model, messages, code_response)
            json_messages = [m.get_message_json(model) for m in messages]

            if debug:
                debug_messages(model, messages)

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

            resp = LlmResponse(
                text=response_text,
                model=model,
                price_total=price_total,
                price_input=price_input,
                price_output=price_output,
                token_count=token_count,
                chat_millis=chat_time
            )

            if output_schema:
                output_schema = common.assert_exists(output_schema)
                with open(output_schema, "r") as schema_file:
                    schema = json.load(schema_file)

                try:
                    jsonschema_validate(instance=resp.json(), schema=schema)
                except Exception:
                    # Attempt to coerce JSON to schema using a cheaper model
                    coerced_json = coerce_json_to_schema(resp.text, schema)
                    resp.parsed_json = coerced_json
                    # Validate again after coercion
                    jsonschema_validate(instance=resp.parsed_json, schema=schema)

                if debug:
                    print(f"""
--------------------------------------
{model} json response (validated against {output_schema}):
{json.dumps(resp.json(), indent=4)}
--------------------------------------""")
            else:
                if debug:
                    print(f"""
--------------------------------------
{model} response:
{response_text}
--------------------------------------""")

            return resp
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
    parsed_json: Optional[dict] = None

    def json(self) -> dict:
        if not self.parsed_json:
            self.parsed_json = ensure_parsable_json(self.text)

        return self.parsed_json


class LlmMessage:
    def __init__(self, message_type: LlmMessageType, text: str, file: Path = None):
        if isinstance(text, Path) and text.exists():
            file = text
            text = None

        self.message_type: LlmMessageType = LlmMessageType(message_type)
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
{self.text}
-----------------------""")

    @staticmethod
    def get_price(model: LlmModel, input_messages: List['LlmMessage'], response_text: str) -> Tuple[float, float, float]:
        usd_per_million_input_token = MODEL_PRICE_USD_PER_MILLION_TOKENS[model]['input']
        usd_per_million_output_token = MODEL_PRICE_USD_PER_MILLION_TOKENS[model]['output']

        price_input = LlmMessage.get_total_token_count(model, input_messages) * usd_per_million_input_token / 1_000_000
        price_output = LlmMessage._get_token_count(model, response_text) * usd_per_million_output_token / 1_000_000

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
        messages_processed = []
        for m in messages:
            if isinstance(m, LlmMessage):
                messages_processed.append(m)
            else:
                messages_processed.append(LlmMessage.user(str(m)))

        return sum([m.get_token_count(model) for m in messages_processed]) + (4 * len(messages_processed))

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
    def parse_prompt(model, messages_in: list['LlmMessage'], code_response=False) -> List['LlmMessage']:
        messages_out = []

        for m in common.filter_none(messages_in):
            if isinstance(m, str):
                if m:
                    messages_out.append(LlmMessage.user(m))
            elif isinstance(m, dict):
                if m:
                    messages_out.append(LlmMessage.user(json.dumps(m, indent=4, cls=ErieIronJSONEncoder)))
            elif isinstance(m, LlmMessage):
                if m.text:
                    messages_out.append(m)
            else:
                raise ValueError(f"invalid message type {m}")

        if code_response:
            messages_out.append(
                LlmMessage(
                    message_type=LlmMessageType.SYSTEM,
                    text="""
respond only with valid code or JSON. do not include any markdown formatting, such as triple backticks or language tags.
if responding with JSON, the property names must be encosed in "double quotes"
                    """
                )
            )

        token_count = LlmMessage.get_total_token_count(model, messages_out)
        while token_count > MODEL_TO_MAX_TOKENS.get(model, sys.maxsize):
            messages_out = messages_out[1:]
            token_count = LlmMessage.get_total_token_count(model, messages_out)

        return messages_out

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
            return json.loads(json_text)
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
                LlmModel.OPENAI_O3_MINI,
                code_response=True
            )
            json_text = llm_response_reformat.text

    raise last_e


def debug_messages(model: LlmModel, messages: list[LlmMessage]):
    print(f"""
--------------- --------------- --------------- --------------- ---------------
Begin chat with {model}
    """)

    for m in common.ensure_list(messages):
        print(f"\n\n\n{m}\n\n\n")

    print("--------------- --------------- --------------- --------------- ---------------")


def coerce_json_to_schema(json_text: str, schema: dict) -> dict:
    prompt = f"""
You are a helpful assistant that receives a JSON text and a JSON schema.
Your task is to correct and coerce the JSON text so that it fully conforms to the provided JSON schema.
Return only the corrected JSON, without any explanations or markdown formatting.

Here is the JSON text:
{json_text}

Here is the JSON schema:
{json.dumps(schema, indent=4)}

Please provide the corrected JSON text:
"""
    last_exception = None
    # Try up to 2 retries with OPENAI_GPT_3_5_TURBO
    for attempt in range(2):
        try:
            response = chat(
                messages=[LlmMessage.user(prompt)],
                model=LlmModel.OPENAI_GPT_3_5_TURBO,
                code_response=True
            )
            coerced_text = response.text
            coerced_json = json.loads(coerced_text)
            return coerced_json
        except Exception as e:
            last_exception = e
    # Fallback once to OPENAI_GPT_4O
    try:
        response = chat(
            messages=[LlmMessage.user(prompt)],
            model=LlmModel.OPENAI_GPT_4o,
            code_response=True
        )
        coerced_text = response.text
        coerced_json = json.loads(coerced_text)
        return coerced_json
    except Exception as e:
        last_exception = e

    # If all attempts fail, raise the last exception
    raise last_exception
