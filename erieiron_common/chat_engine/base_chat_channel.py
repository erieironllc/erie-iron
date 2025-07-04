import uuid
from typing import List, Tuple, Type

from django.db import transaction
from django.db.models import QuerySet

from erieiron_common import models, common
from erieiron_common.chat_engine.chat_channels.llm_chat_context_makers.base_chat_context_maker import BaseChatContextMaker, NullContextMaker
from erieiron_common.chat_engine.prompt import Prompt
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_common.models import ProjectInteraction, Person, Project
from erieiron_common.json_encoder import ErieIronJSONEncoder


class ChatValidationException(Exception):
    def __init__(self, desc, prompt, response):
        self.desc = desc
        self.prompt = prompt
        self.response = response
        super().__init__(f"{desc}: prompt:{prompt} resulted in {response}")


class BaseChatChannel:
    def __init__(self, interaction_id: uuid.UUID):
        self.interaction_id = interaction_id

        self._interaction_features = []
        self._interaction = None
        self._context_maker = None
        self._section = None
        self._parsed_prompt = None
        self._prompt_messages_with_context = None
        self._person = None
        self._project = None
        self._phrase = None
        self._section = None

    def mark_waiting(self, waiting=True):
        with transaction.atomic():
            ProjectInteraction.objects.filter(id=self.interaction_id).update(
                waiting=waiting
            )
            self._interaction = None

    def add_interaction_feature(self, name: str, val: str):
        self._interaction_features.append((name, val))
        return self

    def get_interaction(self) -> ProjectInteraction:
        if not self._interaction:
            self._interaction = models.ProjectInteraction.objects.get(id=self.interaction_id)

        return self._interaction

    def get_previous_interactions(self) -> QuerySet[ProjectInteraction]:
        return self.get_interaction().get_previous_interactions()

    def get_parsed_prompt(self) -> Prompt:
        if not self._parsed_prompt:
            self._parsed_prompt = Prompt.deserialize(self.get_interaction().parsed_prompt)

        return self._parsed_prompt

    def get_prompt_with_context(self) -> list[LlmMessage]:
        if not self._prompt_messages_with_context:
            cm = self.get_context_maker()
            self._prompt_messages_with_context = cm.wrap_prompt_in_context(
                self.get_parsed_prompt()
            )

        return self._prompt_messages_with_context

    def get_project(self) -> Project:
        if not self._project:
            self._project = self.get_interaction().project

        return self._project

    def get_person(self) -> Person:
        if not self._person:
            self._person = self.get_project().person

        return self._person

    def get_context_maker(self) -> BaseChatContextMaker:
        if not self._context_maker:
            context_maker_cls = self.get_context_maker_cls()
            self._context_maker = context_maker_cls(
                person=self.get_person(),
                project=self.get_project()
            )

        return self._context_maker

    def get_context_maker_cls(self) -> Type[BaseChatContextMaker]:
        return NullContextMaker

    def validate_response(self, response: str):
        response = common.default_str(response).lower()

        forbidden_words = self.get_context_maker().get_forbiden_response_words(
            self.get_parsed_prompt().text
        )

        if common.is_empty(forbidden_words):
            return

        if not isinstance(forbidden_words, list):
            forbidden_words = [forbidden_words]

        for fw in forbidden_words:
            s = str(fw)
            if common.is_not_empty(s):
                if str(s).lower() in response:
                    raise ChatValidationException("Found forbidden word in response", s, response)

    def get_chat_response(self):
        raise Exception('must implement chat')

    def post_process_interaction(self, interaction_id: uuid.UUID):
        pass

    def build_chat_response(self) -> Tuple[str, List[Tuple[str, str]], List[uuid.UUID], BaseChatContextMaker]:
        prompt = self.get_parsed_prompt()
        context_maker = self.get_context_maker()

        if prompt.text:
            self.add_interaction_feature(
                "prompt_metadata", ErieIronJSONEncoder.dumps(prompt.serialize())
            ).add_interaction_feature(
                "prompt_with_context", "\n".join([f"{m}\n\n" for m in self.get_prompt_with_context()])
            )

        response = self.get_chat_response()
        if response is None:
            # https://www.youtube.com/watch?v=KH4OnYrZdb0&t=38s
            raise ValueError("no response generated")

        response = str(response)
        self.validate_response(response)
        if response.startswith("**"):
            response = response[2:]
        if response.endswith("**"):
            response = response[:-2]

        self.post_process_interaction(self.interaction_id)

        return response, self._interaction_features, context_maker
