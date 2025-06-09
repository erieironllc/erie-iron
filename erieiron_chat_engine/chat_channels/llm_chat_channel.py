from typing import Type

from erieiron_chat_engine.chat_channels.llm_chat_context_makers.persona_chat_context_maker import BasePersonaContextMaker
from erieiron_chat_engine.base_chat_channel import BaseChatChannel
from erieiron_chat_engine.chat_channels.llm_chat_context_makers.base_chat_context_maker import BaseChatContextMaker
from erieiron_common.llm_apis import llm_interface


class LlmChatChannel(BaseChatChannel):
    def get_chat_response(self) -> str:
        prompt_messages = self.get_prompt_with_context()

        llm_response = llm_interface.chat(prompt_messages)

        self.add_interaction_feature("llm_model", llm_response.model.value)
        self.add_interaction_feature("price_chat", llm_response.price_total)
        self.add_interaction_feature("millis_chat", llm_response.chat_millis)
        self.add_interaction_feature("tokens_chat", llm_response.token_count)

        return llm_response.text

    def get_context_maker_cls(self) -> Type[BaseChatContextMaker]:
        person = self.get_person()

        return BasePersonaContextMaker
