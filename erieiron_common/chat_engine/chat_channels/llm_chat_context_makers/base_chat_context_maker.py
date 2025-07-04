from typing import List

from django.db.models import Q

from erieiron_common import models
from erieiron_common.chat_engine.prompt import Prompt
from erieiron_common.enums import Status
from erieiron_common.llm_apis.llm_interface import LlmMessage


class BaseChatContextMaker:

    def __init__(
            self,
            person: models.Person,
            project: models.Project
    ):
        super().__init__()
        self.person = person
        self.project = project

    def wrap_prompt_in_context(self, prompt: Prompt) -> List[LlmMessage]:
        return [LlmMessage.user(prompt.text)]

    def get_forbiden_response_words(self, prompt):
        return ""

    def get_interactions_for_context(self) -> List[models.ProjectInteraction]:
        interactions = list(reversed(self.project.projectinteraction_set.filter(
            arrangement_section_id__isnull=True,
            phrase_id__isnull=True
        ).exclude(
            Q(channel='SystemCommandChatChannel')
        ).order_by("-created_timestamp")[:30]))

        return interactions

    def get_features_for_context(self, phrase):
        return phrase.feature_set.filter(
            status=Status.COMPLETE
        ).exclude(
            feedback=False,
            feature_type__icontains="yamnet"
        )

    def get_previous_interaction_messages(self) -> list[LlmMessage]:
        messages = []

        # only neutral or positive interactions
        interactions = self.get_interactions_for_context()

        for i in interactions:
            messages.append(LlmMessage.assistant(i.response))

        return messages


class NullContextMaker(BaseChatContextMaker):
    pass
