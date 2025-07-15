from pathlib import Path

from erieiron_common import common
from erieiron_common.common import assert_exists
from erieiron_common.enums import LlmModel
from erieiron_common.llm_apis import llm_interface
from erieiron_common.llm_apis.llm_constants import SYSTEM_AGENT_MODELS_IN_ORDER
from erieiron_common.llm_apis.llm_interface import LlmMessage

BOARD_LEVEL_BASE_PATH = Path("./erieiron_autonomous_agent/board_level_agents/prompts")
BUSINESS_LEVEL_BASE_PATH = Path("./erieiron_autonomous_agent/business_level_agents/prompts")
BASE_PROMPTS_PATH = Path("./erieiron_autonomous_agent/base_prompts")


def board_level_chat(
        system_prompt: str,
        user_messages: list[LlmMessage],
        text_output=False,
        model: LlmModel = None,
        debug=False
):
    system_prompt = assert_exists(BOARD_LEVEL_BASE_PATH / system_prompt)
    
    system_prompts = [
        BOARD_LEVEL_BASE_PATH / "_base_prompt--board_level.md",
        system_prompt
    ]
    
    output_schema = BOARD_LEVEL_BASE_PATH / f"{system_prompt.name}.schema.json"
    
    if not output_schema.exists():
        output_schema = None
    
    return agent_chat(
        system_prompts=system_prompts,
        user_messages=user_messages,
        output_schema=output_schema,
        text_output=text_output,
        model=model,
        debug=debug
    )


def business_level_chat(
        system_prompt: str,
        user_messages: list[LlmMessage],
        text_output=False,
        output_schema: str = None,
        replacements: list[tuple[str, str]] = None,
        model: LlmModel = None,
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
    
    if not output_schema.exists():
        output_schema = None
    
    return agent_chat(
        system_prompts=system_prompts,
        user_messages=user_messages,
        output_schema=output_schema,
        text_output=text_output,
        model=model,
        debug=debug
    )


def agent_chat(
        system_prompts: list[Path],
        user_messages: 'LlmMessage',
        output_schema: Path = None,
        text_output=False,
        model: LlmModel = None,
        debug=False
):
    system_prompts = common.ensure_list(system_prompts)
    system_prompts.append(BASE_PROMPTS_PATH / "_base_prompt--output.md")
    
    messages = []
    for sp in system_prompts:
        if isinstance(sp, LlmMessage):
            messages.append(sp)
        elif isinstance(sp, Path):
            messages.append(LlmMessage.sys(assert_exists(sp)))
        elif isinstance(sp, str):
            messages.append(LlmMessage.sys(sp))
        else:
            raise Exception(f"unhandled prompt type {sp}")
    
    messages += common.ensure_list(user_messages)
    
    if not model:
        model = SYSTEM_AGENT_MODELS_IN_ORDER
    
    resp = llm_interface.chat(
        messages,
        model,
        output_schema=output_schema,
        code_response=False,
        debug=debug
    )
    
    if text_output:
        return resp.text
    else:
        return resp.json()
