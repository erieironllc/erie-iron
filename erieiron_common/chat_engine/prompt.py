import uuid

from erieiron_common import common
from erieiron_common.enums import PromptIntent


class Prompt:
    def __init__(
            self,
            *,
            person_id: uuid.UUID,
            text: str,
            intent: PromptIntent = PromptIntent.UNKNOWN,
            continue_conversation: bool = False,
            count_user_initiated_interactions: int = 0,
            count: int = 1,
            is_command: bool = False,
            is_question: bool = False
    ):
        self.person_id = person_id
        self.text = str(text)
        self.intent = PromptIntent.valid_or(intent)
        self.count = common.parse_int(count)
        self.continue_conversation = common.parse_bool(continue_conversation)
        self.normalized_prompt = Prompt.normalize_text(text)
        self.count_user_initiated_interactions = common.parse_int(count_user_initiated_interactions)
        self.is_command = common.parse_bool(is_command)
        self.is_question = common.parse_bool(is_question)

    def set_normalized_prompt(self, text: str):
        self.normalized_prompt = Prompt.normalize_text(text)

    @staticmethod
    def normalize_text(prompt):
        return common.default_str(prompt).lower().strip()

    def serialize(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def deserialize(cls, data) -> 'Prompt':
        instance = cls.__new__(cls)
        instance.__dict__.update(data)
        return instance

    def __str__(self):
        return self.text
