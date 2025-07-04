from erieiron_common.enums import LlmModel
from erieiron_common.llm_apis import openai_chat_api, gemini_chat_api, claude_chat_api, deepseek_chat_api

SYSTEM_AGENT_MODELS_IN_ORDER = [
    LlmModel.OPENAI_O3,
    LlmModel.GEMINI_2_5_PRO,
    # LlmModel.OPENAI_GPT_4o,
    # LlmModel.OPENAI_GPT_4_1_NANO,
    # LlmModel.OPENAI_O3_PRO,
    # LlmModel.CLAUDE_3_7
]

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
    LlmModel.OPENAI_O3_MINI,
    LlmModel.CLAUDE_3_7,
    LlmModel.DEEPSEEK_CHAT,
]

CODE_PLANNING_MODELS_IN_ORDER = [
    # LlmModel.OPENAI_O3,
    LlmModel.GEMINI_2_5_PRO,
    # LlmModel.OPENAI_GPT_4o,
    # LlmModel.DEEPSEEK_CODER,
    # LlmModel.CLAUDE_3_7 # context window too small
]

MODEL_TO_IMPL = {
    LlmModel.OPENAI_O3_MINI: openai_chat_api,
    LlmModel.OPENAI_O3_PRO: openai_chat_api,
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
    LlmModel.OPENAI_O3_MINI: 200_000,
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
    LlmModel.OPENAI_O3: 30_000,
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
    LlmModel.OPENAI_O3_MINI: {
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
    LlmModel.OPENAI_O3_PRO: {
        "input": 20.00,
        "output": 80.00,
    },
    LlmModel.OPENAI_O3: {
        "input": 2.00,
        "output": 8.00,
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
