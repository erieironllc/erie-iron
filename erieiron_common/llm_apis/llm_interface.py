import json
import logging
import sys
import time
from pathlib import Path
from typing import List

from jsonschema import validate as jsonschema_validate

from erieiron_common import common
from erieiron_common.enums import LlmModel, LlmMessageType, LlmReasoningEffort, LlmVerbosity
from erieiron_common.json_encoder import ErieIronJSONEncoder
from erieiron_common.llm_apis.llm_constants import MODEL_TO_IMPL, MODEL_TO_MAX_TOKENS, get_token_count
from erieiron_common.llm_apis.llm_response import LlmResponse


def chat(
        messages: list['LlmMessage'],
        model: LlmModel = None,
        output_schema: Path = None,
        code_response=False,
        reasoning_effort: LlmReasoningEffort = None,
        verbosity: LlmVerbosity = None,
        debug=False
) -> 'LlmResponse':
    messages = common.flatten(messages)
    
    if messages and output_schema and output_schema.exists():
        code_response = True
        messages = [
            messages[0],
            *common.ensure_list(
                LlmMessage.sys_from_data("The output json will be validated against this schema", json.loads(output_schema.read_text()))
            ),
            *messages[1:]
        ]
    
    if not model:
        models = [LlmModel.OPENAI_GPT_5_1]
    else:
        models = common.ensure_list(model)
    
    for idx, model in enumerate(models):
        if not isinstance(model, LlmModel):
            model = LlmModel(model)
        
        try:
            impl = MODEL_TO_IMPL[model]
            
            messages = LlmMessage.parse_prompt(model, messages, code_response)
            json_messages = [m.get_message_json(model) for m in messages]
            
            start_time = time.time()
            resp = impl.chat(
                json_messages,
                model,
                code_response,
                reasoning_effort,
                verbosity
            )
            chat_time = (time.time() - start_time) * 1000
            
            if not isinstance(resp, LlmResponse):
                resp = LlmResponse(
                    text=resp,
                    model=model,
                    input_token_count=LlmMessage.get_total_token_count(model, messages),
                    output_token_count=get_token_count(model, resp),
                    chat_millis=chat_time
                )
            
            if output_schema:
                output_schema = common.assert_exists(output_schema)
                with open(output_schema, "r") as schema_file:
                    schema = json.load(schema_file)
                
                for i in range(5):
                    try:
                        jsonschema_validate(instance=resp.json(), schema=schema)
                        break
                    except Exception as e:
                        # Attempt to coerce JSON to schema using a cheaper model
                        resp.parsed_json = coerce_json_to_schema(resp.text, schema, e)
            
            return resp
        except Exception as e:
            is_last = idx == len(models) - 1
            if is_last:
                raise e
            else:
                logging.exception(f"chat with {model} failed.  will try {models[idx + 1]}")


def sanitize_prompt(raw_text: str) -> str:
    if isinstance(raw_text, dict):
        return json.dumps(raw_text, indent=4, cls=ErieIronJSONEncoder)
    else:
        return raw_text


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
    
    def get_message_json(self, model: LlmModel) -> dict:
        model = LlmModel(model)
        
        role_str = self.message_type.value
        if model in [LlmModel.GEMINI_3_0_PRO, LlmModel.GEMINI_3_0_FLASH, LlmModel.GEMINI_2_5_PRO, LlmModel.GEMINI_2_0_FLASH]:
            if LlmMessageType.SYSTEM.eq(self.message_type):
                role_str = "user"
            elif LlmMessageType.ASSISTANT.eq(self.message_type):
                role_str = "model"
        elif model in [LlmModel.CLAUDE_4_5, LlmModel.CLAUDE_3_7, LlmModel.CLAUDE_3_5]:
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
            elif m:
                messages_processed.append(LlmMessage.user(str(m)))
        
        return sum([m.get_token_count(model) for m in messages_processed]) + (4 * len(messages_processed))
    
    def get_token_count(self, model: LlmModel) -> int:
        try:
            return get_token_count(
                model,
                json.dumps(self.get_message_json(model), cls=ErieIronJSONEncoder)
            )
        except Exception as e:
            logging.exception(e)
    
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
                    if isinstance(m.text, dict):
                        messages_out.append(LlmMessage.user(json.dumps(m.text, indent=4, cls=ErieIronJSONEncoder)))
                    else:
                        messages_out.append(m)
            else:
                raise ValueError(f"invalid message type {m}")
        
        # if code_response:
        #     messages_out.append(
        #         LlmMessage(
        #             message_type=LlmMessageType.SYSTEM,
        #             text="""
        # respond only with valid code or JSON. do not include any markdown formatting, such as triple backticks or language tags.
        # if responding with JSON, the property names must be encosed in "double quotes"
        #                     """
        #                 )
        #             )
        
        token_count = LlmMessage.get_total_token_count(model, messages_out)
        max_token_count = MODEL_TO_MAX_TOKENS.get(model, sys.maxsize)
        if token_count > max_token_count:
            raise Exception(f"token count greater than {model}'s max: {token_count} > {max_token_count}")
        
        return messages_out
    
    @classmethod
    def assistant(cls, txt, file=None) -> 'LlmMessage':
        return LlmMessage(
            message_type=LlmMessageType.ASSISTANT,
            text=txt,
            file=file
        )
    
    @classmethod
    def user(cls, txt, file=None) -> 'LlmMessage':
        return LlmMessage(
            message_type=LlmMessageType.USER,
            text=txt,
            file=file
        )
    
    @classmethod
    def user_from_data(cls, title, data, item_name=None) -> list['LlmMessage']:
        if not data:
            return []
        
        return [LlmMessage.user(
            cls._get_data_string(title, data, item_name=None)
        )]
    
    @classmethod
    def sys(cls, txt, file=None) -> 'LlmMessage':
        return LlmMessage(
            message_type=LlmMessageType.SYSTEM,
            text=txt,
            file=file
        
        )
    
    @classmethod
    def sys_from_data(cls, title, data) -> list['LlmMessage']:
        if not data:
            return []
        
        return [LlmMessage.sys(
            cls._get_data_string(title, data)
        )]
    
    @classmethod
    def _get_data_string(cls, title, data, item_name=None):
        from django.db.models import Model
        if common.is_list_like(data):
            data = {
                f"{item_name or 'items'}": common.ensure_list(data)
            }
        elif isinstance(data, Model):
            data = common.get_dict(data)
        elif isinstance(data, Path):
            label = item_name or "content"
            path_str = str(data)
            if any(path_str.endswith(s) for s in ["Dockerfile", "requirements.txt", ".json", ".yaml", ".html", ".py", ".js", ".css", ".sql"]):
                label = item_name or "code"
            
            data = {
                f"{label}": data.read_text() if data.exists() else f"{data} does not exist"
            }
        elif isinstance(data, dict):
            ...
        else:
            data = {
                f"{item_name or 'contents'}": str(data)
            }
        
        for title_name in ["description", "desc", "title", "name", "summary"]:
            if title_name not in data:
                break
        
        data_string = json.dumps({
            f"{title_name}": title,
            **data
        }, indent=4, cls=ErieIronJSONEncoder)
        
        return data_string
    
    @classmethod
    def dumps(cls, messages: list['LlmMessage']):
        strings = []
        for m in common.ensure_list(messages):
            strings.append(f"""
========= Message Type: {m.message_type.label()} ==========
{m.text}
            """)
        return "\n\n".join(strings)
    
    @classmethod
    def log(cls, messages: list['LlmMessage']):
        print(cls.dumps(messages))


def coerce_json_to_schema(json_text: str, schema: dict, e) -> dict:
    if isinstance(e, Exception):
        e = common.get_stack_trace_as_string(e)
    else:
        e = str(e)
        
    messages = [
        LlmMessage.sys(
            f"""
            You are an expert in writing valid JSON which comports to a specific JSON Schema.
            
            ## Inputs
            You receive
                - JSON text
                - a JSON schema
                - an Error Message.  

            The JSON text is failing validation against the JSON schema as described by the Error Message.

            ## Task
            Your task is to correct and coerce the JSON text so that it fully conforms to the provided JSON schema.
            
            ## Output
            Return only the corrected JSON, without any explanations or markdown formatting. """
        ),
        LlmMessage.user_from_data("JSON Data", {
            "JSON text": json_text,
            "JSON schema": schema,
            "Error Message": str(e)
        })
    ]
    
    last_exception = e
    # Try up to 2 retries with OPENAI_GPT_3_5_TURBO
    for attempt in range(2):
        try:
            logging.error(f"fixing invalid json - attempt {attempt + 1}")
            
            response = chat(
                messages=messages,
                model=LlmModel.OPENAI_GPT_5_MINI,
                verbosity=LlmVerbosity.LOW,
                reasoning_effort=LlmVerbosity.HIGH,
                code_response=True
            )
            
            return json.loads(response.text)
        except Exception as e:
            logging.exception(e)
            last_exception = e
    
    # Fallback once to OPENAI_GPT_4O
    try:
        logging.error(f"fixing invalid json - final attempt")
        response = chat(
            messages=messages,
            model=LlmModel.OPENAI_GPT_5_1,
            verbosity=LlmVerbosity.LOW,
            reasoning_effort=LlmVerbosity.HIGH,
            code_response=True
        )
        return json.loads(response.text)
    except Exception as e:
        logging.exception(e)
        last_exception = e
    
    # If all attempts fail, raise the last exception
    raise last_exception
