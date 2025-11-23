import json
import uuid

from erieiron_common import common
from erieiron_common.enums import LlmModel, LlmReasoningEffort, LlmVerbosity, LlmCreativity


class LlmResponse:
    def __init__(
            self,
            text: str,
            model: LlmModel,
            input_token_count: int,
            output_token_count: int,
            chat_millis: float,
            parsed_json: dict = None
    ):
        from erieiron_common.llm_apis.llm_constants import MODEL_PRICE_USD_PER_MILLION_TOKENS
        super().__init__()
        
        self.parsed_json = parsed_json
        self.text = post_process_response(text)
        self.model = model
        self.input_token_count = input_token_count
        self.output_token_count = output_token_count
        self.token_count = input_token_count + output_token_count
        self.chat_millis = chat_millis
        
        model_pricing = MODEL_PRICE_USD_PER_MILLION_TOKENS[model]
        self.price_input = input_token_count * model_pricing['input'] / 1_000_000
        self.price_output = output_token_count * model_pricing['output'] / 1_000_000
        self.price_total = self.price_input + self.price_output
        self.llm_request_id: str = None
    
    def set_llm_request_id(self, llm_request_id: uuid.UUID):
        self.llm_request_id = str(llm_request_id)
    
    def json(self) -> dict:
        if not self.parsed_json:
            self.parsed_json = ensure_parsable_json(self.text)
        
        return self.parsed_json


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
            last_e = e
            from erieiron_common.llm_apis.llm_interface import chat
            llm_response_reformat = chat(
                f"""
please format and return the following json text as valid and parsable json:

========= json text start ================
{json_text}
========= json text end ================


the previous attempt at parsing this content resulted in this error:  {e}


resond only with parsable json.  do not include any comments, explanations, or non-json markdown
""",
                model=LlmModel.OPENAI_GPT_5_NANO,
                reasoning_effort=LlmReasoningEffort.LOW,
                verbosity=LlmVerbosity.LOW,
                creativity=LlmCreativity.LOW,
                code_response=True
            )
            json_text = llm_response_reformat.text
    
    raise last_e
