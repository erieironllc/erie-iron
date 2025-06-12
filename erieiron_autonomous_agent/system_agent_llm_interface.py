from pathlib import Path

from erieiron_common import common
from erieiron_common.common import assert_exists
from erieiron_common.llm_apis import llm_interface
from erieiron_common.llm_apis.llm_constants import SYSTEM_AGENT_MODELS_IN_ORDER
from erieiron_common.llm_apis.llm_interface import LlmMessage

BOARD_LEVEL_BASE_PATH = Path("./erieiron_autonomous_agent/board_level_agents/prompts")
BUSINESS_LEVEL_BASE_PATH = Path("./erieiron_autonomous_agent/business_level_agents/prompts")
BASE_PROMPTS_PATH = Path("./erieiron_autonomous_agent/base_prompts")


def board_level_chat(
        system_prompts: list[str],
        user_messages: list[LlmMessage],
        output_schema: str = None,
        text_output=False,
        debug=False
):
    system_prompts = [BOARD_LEVEL_BASE_PATH / s for s in common.ensure_list(system_prompts)]
    system_prompts.append(BOARD_LEVEL_BASE_PATH / "_base_prompt--board_level.md")

    if output_schema:
        output_schema = BOARD_LEVEL_BASE_PATH / output_schema

    return agent_chat(system_prompts, user_messages, output_schema, text_output, debug)


def business_level_chat(
        system_prompts: list[str],
        user_messages: list[LlmMessage],
        output_schema: str = None,
        text_output=False,
        debug=False
):
    system_prompts = [BUSINESS_LEVEL_BASE_PATH / s for s in common.ensure_list(system_prompts)]
    # system_prompts.append(BUSINESS_LEVEL_BASE_PATH / "_base_prompt--business.md")

    if output_schema:
        output_schema = BUSINESS_LEVEL_BASE_PATH / output_schema

    return agent_chat(system_prompts, user_messages, output_schema, text_output, debug)


def agent_chat(
        system_prompts: list[Path],
        user_messages: 'LlmMessage',
        output_schema: Path = None,
        text_output=False,
        debug=False
):
    system_prompts = common.ensure_list(system_prompts)
    system_prompts.append(BASE_PROMPTS_PATH / "_base_prompt--output.md")

    messages = [LlmMessage.sys(assert_exists(p)) for p in system_prompts]
    messages += common.ensure_list(user_messages)

    resp = llm_interface.chat(
        messages,
        SYSTEM_AGENT_MODELS_IN_ORDER,
        output_schema=output_schema,
        code_response=False,
        debug=debug
    )

    if text_output:
        return resp.text
    else:
        return resp.json()
