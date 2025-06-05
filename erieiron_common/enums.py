import datetime
import random
import uuid
from enum import Enum
from typing import List, Tuple


class ErieEnum(Enum):
    @classmethod
    def to_dict(cls):
        return {str(key.name): str(key.value) for key in cls}

    def label(self):
        return str(self.value).replace("_", " ").capitalize()

    def neq(self, v):
        return not self.eq(v)

    def eq(self, v):
        if v is None:
            return False

        if self == v:
            return True

        try:
            return self.__class__(v) == self
        except:
            return False

    def __str__(self):
        return str(self.value)

    @classmethod
    def to_list(cls, values):
        from erieiron_common import common

        vals = []
        for s in common.ensure_list(values):
            if cls.valid(s):
                vals.append(cls(s))

        return vals

    @classmethod
    def to_value_list(cls, values):
        from erieiron_common import common

        return [cls(s).value for s in common.ensure_list(values) if cls.valid(s)]

    @classmethod
    def valid(cls, value):
        try:
            return cls(value).value is not None
        except:
            return False

    @classmethod
    def valid_or(cls, value, default_val=None):
        try:
            return cls(value)
        except:
            return default_val

    @classmethod
    def random(cls):
        return random.choice([k for k in cls])

    @classmethod
    def choices(cls):
        return [(key.value, key.label if isinstance(key.label, str) else key.label()) for key in cls if not key.name.startswith("_")]


class SystemAgentTask(ErieEnum):
    GENERATE_IDEA = "generate_idea"
    REVIEW_BUSINESSES = "review_businesses"
    FLESH_OUT_IDEA = "flesh_out_idea"
    LEGAL_REVIEW = "legal_review"


class SystemCapacity(ErieEnum):
    AVAILABLE = "available"
    CAPPED = "capped"
    OVERLOAD = "overload"


class PromptIntent(ErieEnum):
    UNKNOWN = "unknown"
    CONTINUE_PREVIOUS_CONVERSATION = "continue_previous_conversation"
    ERIE_GENERATED = "erie_generated"


class Role(ErieEnum):
    SYSTEM = "system"  # this is not meant to be a human user.  used for things like the shared library
    DEVELOPER = "developer"
    ADMIN = "admin"
    BETA_USER = "beta_user"
    SUBSCRIBER_PRO = "subscriber_pro"
    SUBSCRIBER = "subscriber"
    FREE_USER = "free_user"
    BANNED = "banned"

    @staticmethod
    def roles_in_order():
        # each subsequent role inherits the privs of the previous role
        return [
            Role.BANNED,
            Role.FREE_USER,
            Role.SUBSCRIBER,
            Role.SUBSCRIBER_PRO,
            Role.BETA_USER,
            Role.ADMIN,
            Role.DEVELOPER,
            Role.SYSTEM
        ]

    @classmethod
    def choices(cls):
        return [(role.value, role.label()) for role in Role.roles_in_order()]


class Status(ErieEnum):
    NOT_STARTED = "not started"
    IN_PROGRESS = "in progress"
    COMPLETE = "complete"
    WONT_DO = "won't do"
    ERROR = "error"


class SystemPersonIds(ErieEnum):
    TRAINING_DATA = uuid.UUID('00000000-0000-0000-0000-000000000000')


class PubSubHandlerInstanceStatus(ErieEnum):
    AVAILABLE = 'available'
    NOT_AVAILABLE = 'not_available'
    KILL_REQUESTED = 'kill_requested'


class PubSubMessageStatus(ErieEnum):
    PENDING = 'pending'
    PROCESSING = 'processing'
    PROCESSED = 'processed'
    FAILED = 'failed'
    OBSOLETE = 'obsolete'
    NO_CONSUMER = 'no_consumer'

    @staticmethod
    def inprog_statuses() -> List['PubSubMessageStatus']:
        return [PubSubMessageStatus.PENDING, PubSubMessageStatus.PROCESSING]


class PubSubMessagePriority(ErieEnum):
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"

    @staticmethod
    def get_priorities_in_order() -> 'PubSubMessagePriority':
        return [
            PubSubMessagePriority.HIGH,
            PubSubMessagePriority.NORMAL,
            PubSubMessagePriority.LOW,
        ]

    @staticmethod
    def get_weighted_random_priorty() -> 'PubSubMessagePriority':
        weights = {
            (PubSubMessagePriority.HIGH, .45),
            (PubSubMessagePriority.NORMAL, .3),
            (PubSubMessagePriority.LOW, .25)
        }

        # Use random.choices to select a priority based on weights
        return random.choices([w[0] for w in weights], weights=[w[1] for w in weights], k=1)[0]

    @staticmethod
    def get_weighted_random_priorities() -> List['PubSubMessagePriority']:
        import math
        import random
        weights = {
            PubSubMessagePriority.HIGH: 0.45,
            PubSubMessagePriority.NORMAL: 0.3,
            PubSubMessagePriority.LOW: 0.25
        }

        scored_priorities = [(priority, -math.log(random.random()) / weight) for priority, weight in weights.items()]
        sorted_priorities = sorted(scored_priorities, key=lambda x: x[1])

        return [priority for priority, _ in sorted_priorities]


class ClientMessage(ErieEnum):
    INTERACTION_RESPONSE_UPDATED = "interaction_response_updated"


class PubSubMessageType(ErieEnum):
    CHAT_INTERACTION_INITIATED = "chat_interaction_initiated"
    CHAT_CHANNEL_LLM = "chat_channel_llm"

    @staticmethod
    def get_cuda_only_message_types() -> Tuple['PubSubMessageType']:
        return {
        }

    def get_namespace(self, namespace_id):
        if namespace_id is None:
            return self.__class__.__name__

        return f"{self}_{namespace_id}"


class ScaleAction(ErieEnum):
    INCREASE = "increase"
    NO_CHANGE = "no_change"
    DECREASE = "decrease"
    GO_TO_ZERO = "go_to_zero"


class AutoScalingGroup(ErieEnum):
    MESSAGE_PROCESSOR = "messageprocessor-ecs-asg"
    WEBSERVICE = "ui-ecs-asg"


class PersonAuthStatus(ErieEnum):
    NOT_LOGGED_IN = "not_logged_in"
    BANNED = "banned"
    NOT_INVITED = "not_invited"
    INVITED_BUT_NOT_CONFIRMED = "invite_but_not_confirmed"
    ALL_GOOD = "all_good"


class DataSplit(ErieEnum):
    TRAIN = "train"
    TEST = "test"
    VALIDATE = "validate"


class ReportingPeriod(ErieEnum):
    WEEK = 7
    THIRTY_DAYS = 30
    NINETY_DAYS = 90

    def get_start_date(self):
        return datetime.date.today() - datetime.timedelta(days=self.value)

    def get_dates(self):
        dates = [
            datetime.date.today() - datetime.timedelta(days=i)
            for i in range(self.value)
        ]
        dates.reverse()
        return dates


class LlmRole(ErieEnum):
    ML_ENGINEER = "ml_engineer"

    @staticmethod
    def get_role_desc(role: 'LlmRole') -> str:
        if LlmRole.ML_ENGINEER.eq(role.value):
            return "You are an expert in machine learning, audio analysis, and writing super clean python code"
        else:
            raise Exception(f"unhandled role {role}")


class LlmMessageType(ErieEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class LlmModel(ErieEnum):
    OPENAI_GPT_3_5_TURBO = "gpt-3.5-turbo"
    OPENAI_GPT_4_1 = "gpt-4.1"
    OPENAI_GPT_4_1_MINI = "gpt-4.1-mini"
    OPENAI_GPT_4_1_NANO = "gpt-4.1-nano"
    OPENAI_GPT_4_5 = "gpt-4.5"
    OPENAI_GPT_4_TURBO = "gpt-4-turbo"
    OPENAI_GPT_4o = "gpt-4o"
    OPENAI_GPT_4o_20240806 = "gpt-4o-2024-08-06"
    OPENAI_GPT_O3_MINI = "o3-mini-2025-01-31"
    OPENAI_O1 = "o1"
    OPENAI_O1_MINI = "o1-mini"
    OPENAI_O3 = "o3"
    OPENAI_O4 = "o4"
    OPENAI_O4_MINI = "o4-mini"
    OPENAI_GPT_45_DO_NOT_USE_VERY_VERY_EXPENSIVE = "gpt-4.5-preview-2025-02-27"

    GEMINI_2_5_PRO = "gemini-2.5-pro-preview-03-25"
    GEMINI_2_0_FLASH = "gemini-2.0-flash"

    CLAUDE_3_7 = "claude-3-7-sonnet-20250219"
    CLAUDE_3_5 = "claude-3-5-sonnet-20240620"
    CLAUDE_3_OPUS_DO_NOT_USE_VERY_EXPENSIVE = "claude-3-opus-20240229"

    DEEPSEEK_CODER = "deepseek-coder"
    DEEPSEEK_CHAT = "deepseek-chat"


class S3Bucket(ErieEnum):
    MODELS = "MODELS"


class ConsentChoice(ErieEnum):
    ACCEPTED = "accepted"
    DECLINED = "declined"

    @staticmethod
    def from_bool(bool_val) -> "ConsentChoice":
        from erieiron_common import common
        return ConsentChoice.ACCEPTED if common.parse_bool(bool_val) else ConsentChoice.DECLINED
