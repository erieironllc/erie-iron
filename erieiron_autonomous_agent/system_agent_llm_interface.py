import logging
import os
import random
import traceback
from pathlib import Path

import settings
from erieiron_autonomous_agent.enums import BusinessOperationType
from erieiron_autonomous_agent.models import LlmRequest, Business, Initiative, SelfDrivingTaskIteration
from erieiron_common import common
from erieiron_common.common import assert_exists
from erieiron_common.enums import LlmModel, LlmReasoningEffort, LlmVerbosity, LlmCreativity
from erieiron_common.llm_apis.llm_constants import MODEL_BACKUPS, MODEL_TO_MAX_TOKENS
from erieiron_common.llm_apis.llm_interface import LlmMessage, chat, sanitize_prompt
from erieiron_common.llm_apis.llm_response import LlmResponse

PROMPTS_DIR = BOARD_LEVEL_BASE_PATH = BUSINESS_LEVEL_BASE_PATH = BASE_PROMPTS_PATH = Path(os.getcwd()) / "prompts"


def board_level_chat(
        description,
        system_prompts: list[str],
        user_messages: list[LlmMessage],
        text_output=False,
        model: LlmModel = None,
        business: Business = None,
        reasoning_effort=LlmReasoningEffort.MEDIUM,
        verbosity=LlmVerbosity.MEDIUM,
        creativity=LlmCreativity.MEDIUM,
        debug=False
):
    output_schema = None
    system_prompt_paths = []
    for system_prompt in common.filter_none(common.ensure_list(system_prompts)):
        system_prompt_path = BOARD_LEVEL_BASE_PATH / system_prompt
        system_prompt_paths.append(
            assert_exists(system_prompt_path)
        )
        
        schema = BOARD_LEVEL_BASE_PATH / f"{system_prompt_path.name}.schema.json"
        if not output_schema and schema.exists():
            output_schema = schema
    
    if business and BusinessOperationType.is_manual(business.operation_type):
        system_prompt_paths.append(
            BOARD_LEVEL_BASE_PATH / "_base_prompt--board_level--manual.md"
        )
    else:
        system_prompt_paths.append(
            BOARD_LEVEL_BASE_PATH / "_base_prompt--board_level.md"
        )
    
    if business:
        system_prompt_paths.append(
            f"The business operation type is `{'Third-Party' if BusinessOperationType.is_thirdparty(business.operation_type) else 'Erie Iron Portfolio'}` ({business.operation_type})"
        )
    
    return agent_chat(
        description=description,
        tag_entity=business or Business.get_erie_iron_business(),
        system_prompts=system_prompt_paths,
        user_messages=user_messages,
        output_schema=output_schema,
        text_output=text_output,
        model=model,
        reasoning_effort=reasoning_effort,
        verbosity=verbosity,
        creativity=creativity,
        debug=debug
    )


def business_level_chat(
        tag_entity,
        description: str,
        system_prompt: str,
        user_messages: list[LlmMessage],
        text_output=False,
        output_schema: str = None,
        replacements: list[tuple[str, str]] = None,
        model: LlmModel = None,
        reasoning_effort=LlmReasoningEffort.MEDIUM,
        verbosity=LlmVerbosity.MEDIUM,
        creativity=LlmCreativity.MEDIUM,
        debug=False
):
    if output_schema:
        output_schema = BUSINESS_LEVEL_BASE_PATH / output_schema
    else:
        output_schema = BUSINESS_LEVEL_BASE_PATH / f"{common.ensure_list(system_prompt)[0]}.schema.json"
    
    system_prompts = []
    for sp in common.ensure_list(system_prompt):
        sp = assert_exists(BUSINESS_LEVEL_BASE_PATH / sp)
        
        msg = sp.read_text()
        for look_for_str, replace_with_str in common.ensure_list(replacements):
            msg = msg.replace(look_for_str, replace_with_str)
        
        system_prompts.append(LlmMessage.sys(msg))
    
    if not text_output:
        system_prompts.append("**you must** respond with well pure json and nothing else.  no headers, no ```json, etc.  must be immediately parsable json")
    
    if not output_schema.exists():
        output_schema = None
    
    return agent_chat(
        tag_entity=tag_entity,
        description=description,
        system_prompts=system_prompts,
        user_messages=user_messages,
        output_schema=output_schema,
        text_output=text_output,
        model=model,
        reasoning_effort=reasoning_effort,
        verbosity=verbosity,
        creativity=creativity,
        debug=debug
    )


def agent_chat(
        description: str,
        system_prompts: list[Path],
        user_messages: 'LlmMessage',
        tag_entity,
        output_schema: Path = None,
        text_output=False,
        model: LlmModel = None,
        reasoning_effort=LlmReasoningEffort.MEDIUM,
        verbosity=LlmVerbosity.MEDIUM,
        creativity=None,
        debug=False

):
    system_prompts = common.ensure_list(system_prompts)
    system_prompts.append(BASE_PROMPTS_PATH / "_base_prompt--output.md")
    
    if text_output:
        creativity = creativity or LlmCreativity.MEDIUM
        system_prompts.append(LlmMessage.sys("""
        # OUTPUT FORMAT (Required Rule)
        **you must** format the output in markdown syntax.  **Do not** return a json datastructure
        """))
    else:
        creativity = creativity or LlmCreativity.NONE
        system_prompts.append(LlmMessage.sys("""
        # OUTPUT FORMAT (Required Rule)
        **you must** format the as pure JSON with no header or footer content.  The output **must** be immediately parsable as JSON
        """))
    
    system_prompt_message_texts = []
    for sp in system_prompts:
        if isinstance(sp, LlmMessage):
            system_prompt_message_texts.append(sp.text)
        elif isinstance(sp, Path):
            system_prompt_message_texts.append(assert_exists(sp).read_text())
        elif isinstance(sp, str):
            system_prompt_message_texts.append(sp)
        else:
            raise Exception(f"unhandled prompt type {sp}")
    
    messages = [
                   LlmMessage.sys("\n\n".join(system_prompt_message_texts))
               ] + common.ensure_list(user_messages)
    
    if not model:
        model = get_reasoning_model()
    
    resp = llm_chat(
        description,
        messages,
        tag_entity=tag_entity,
        model=model,
        output_schema=output_schema,
        code_response=not text_output,
        reasoning_effort=reasoning_effort,
        verbosity=verbosity,
        creativity=creativity
    )
    
    if text_output:
        return resp.text
    else:
        return resp.json()


def llm_chat(
        description: str,
        messages: list[LlmMessage],
        tag_entity,
        model: LlmModel = LlmModel.OPENAI_GPT_5_MINI,
        output_schema=None,
        reasoning_effort: LlmReasoningEffort = LlmReasoningEffort.LOW,
        verbosity: LlmVerbosity = LlmVerbosity.LOW,
        creativity: LlmCreativity = LlmCreativity.NONE,
        code_response=False
) -> LlmResponse:
    input_model = model
    messages = common.flatten(messages)
    
    if not creativity:
        creativity = LlmCreativity.NONE if code_response else LlmCreativity.MEDIUM
    
    if isinstance(tag_entity, SelfDrivingTaskIteration):
        business = tag_entity.self_driving_task.business
        initiative = tag_entity.self_driving_task.task.initiative
        iteration = tag_entity
    elif isinstance(tag_entity, Initiative):
        business = tag_entity.business
        initiative = tag_entity
        iteration = None
    elif isinstance(tag_entity, Business):
        business = tag_entity
        initiative = None
        iteration = None
    elif tag_entity is None:
        raise ValueError(f"missing tag entity")
    else:
        raise ValueError(f"invalid tag entity {tag_entity}")
    
    token_count = LlmMessage.get_total_token_count(model, messages)
    
    llm_resp = None
    llm_request_url = ""
    for i in range(2):
        llm_messages = LlmMessage.parse_prompt(
            model,
            messages,
            code_response=code_response
        )
        
        llm_request = LlmRequest.objects.create(
            title=description,
            reasoning_effort=reasoning_effort,
            verbosity=verbosity,
            creativity=creativity,
            business=business,
            initiative=initiative,
            task_iteration=iteration,
            llm_model=model.value,
            output_schema=common.safe_read(output_schema),
            token_count=0,
            price=0,
            input_messages=[{
                "role": m.message_type.value,
                "content": sanitize_prompt(m.text)
            } for m in llm_messages]
        )
        llm_request_url = f"{settings.BASE_URL}/llm/debug/{llm_request.id}"
        
        if output_schema:
            logging.info(f"llm chat start: {description} ({output_schema}); Model:{model}; Reasoning: {reasoning_effort}; Verbosity: {verbosity}; Creativity: {creativity}, {llm_request_url}")
        else:
            logging.info(f"llm chat start: {description}; Model:{model}; Reasoning: {reasoning_effort}; Verbosity: {verbosity}; Creativity: {creativity}; {llm_request_url}")
        
        try:
            max_tokens = MODEL_TO_MAX_TOKENS.get(model)
            llm_resp = chat(
                messages=llm_messages,
                model=model,
                output_schema=PROMPTS_DIR / output_schema if output_schema else None,
                code_response=code_response,
                reasoning_effort=reasoning_effort,
                verbosity=verbosity,
                creativity=creativity,
                debug=False
            )
            llm_resp.set_llm_request_id(llm_request.id)
            
            resp_json = llm_resp.__dict__.copy()
            resp_json.pop("text", None)
            resp_json.pop("parsed_json", None)
            
            LlmRequest.objects.filter(id=llm_request.id).update(
                llm_model=llm_resp.model,
                chat_millis=llm_resp.chat_millis,
                response=llm_resp.text,
                resp_json=resp_json,
                token_count=llm_resp.token_count,
                price=llm_resp.price_total
            )
            llm_request.refresh_from_db(fields=["llm_model", "chat_millis", "response", "resp_json", "token_count", "price"])
            
            break
        except Exception as e:
            logging.exception(e)
            LlmRequest.objects.filter(id=llm_request.id).update(
                response=traceback.format_exc(),
                token_count=0,
                price=0
            )
            
            if i == 1:
                raise e
            else:
                model = MODEL_BACKUPS.get(model)
                if not model:
                    raise e
    
    if output_schema:
        logging.info(f"llm chat complete: {description} ({output_schema}); {llm_request_url}")
    else:
        logging.info(f"llm chat complete: {description}; {llm_request_url}")
    
    if LlmModel(llm_resp.model) != LlmModel(input_model):
        logging.info(f"INPUT MODEL DIFFERENT {input_model} THAN OUTPUT MODEL {llm_resp.model}; {llm_request_url}")
    
    return llm_resp


def get_sys_prompt(
        file_name: str,
        replacements: tuple[str, str] = None
) -> LlmMessage:
    return_list = common.is_list_like(file_name)
    
    messages = []
    for f in common.ensure_list(file_name):
        msg = (PROMPTS_DIR / f).read_text()
        for look_for_str, replace_with_str in common.ensure_list(replacements):
            msg = msg.replace(look_for_str, replace_with_str)
        messages.append(msg)
    
    return LlmMessage.sys("\n\n-------\n\n".join(messages))


def get_reasoning_model() -> LlmModel:
    return random.choices(
        [LlmModel.OPENAI_GPT_5_1, LlmModel.GEMINI_3_0_PRO, LlmModel.CLAUDE_4_5],
        # weights=[0.6, 0.25, 0.15],
        weights=[0.6, 0, 0.6],
        k=1
    )[0]
