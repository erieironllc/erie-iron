import datetime
import logging
import random
from enum import StrEnum, auto
from typing import List, Tuple

DEV_STACK_TOKEN_LENGTH = 5


class ErieEnum(StrEnum):
    # noinspection PyMethodParameters
    def _generate_next_value_(name, start, count, last_values):
        return name
    
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


class SystemCapacity(ErieEnum):
    AVAILABLE = auto()
    CAPPED = auto()
    OVERLOAD = auto()


class ComputeDevice(ErieEnum):
    CPU = "cpu"
    MPS = "mps"
    CUDA = "cuda"
    
    def __str__(self):
        return self.value
    
    @classmethod
    def has_gpu(cls, device: 'ComputeDevice'):
        return ComputeDevice.MPS.eq(device) or ComputeDevice.CUDA.eq(device)


class PromptIntent(ErieEnum):
    UNKNOWN = auto()
    CONTINUE_PREVIOUS_CONVERSATION = auto()
    ERIE_GENERATED = auto()


class Role(ErieEnum):
    SYSTEM = auto()
    DEVELOPER = auto()
    ADMIN = auto()
    BETA_USER = auto()
    SUBSCRIBER_PRO = auto()
    SUBSCRIBER = auto()
    FREE_USER = auto()
    BANNED = auto()
    
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


class GoalStatus(ErieEnum):
    ON_TRACK = auto()
    AT_RISK = auto()
    OFF_TRACK = auto()


class Status(ErieEnum):
    NOT_STARTED = auto()
    IN_PROGRESS = auto()
    COMPLETE = auto()
    WONT_DO = auto()
    ERROR = auto()


class SystemPersonIds(ErieEnum):
    TRAINING_DATA = '00000000-0000-0000-0000-000000000000'


class PubSubHandlerInstanceStatus(ErieEnum):
    AVAILABLE = auto()
    NOT_AVAILABLE = auto()
    KILL_REQUESTED = auto()


class PubSubMessageStatus(ErieEnum):
    PENDING = auto()
    PROCESSING = auto()
    PROCESSED = auto()
    FAILED = auto()
    OBSOLETE = auto()
    NO_CONSUMER = auto()
    
    @staticmethod
    def inprog_statuses() -> List['PubSubMessageStatus']:
        return [PubSubMessageStatus.PENDING, PubSubMessageStatus.PROCESSING]


class PubSubMessagePriority(ErieEnum):
    HIGH = auto()
    NORMAL = auto()
    LOW = auto()
    
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
    INTERACTION_RESPONSE_UPDATED = auto()
    LLM_RESPONSE_READY = auto()


class PubSubWorkerResponse(ErieEnum):
    STOP = auto()


class PubSubMessageType(ErieEnum):
    EVERY_MINUTE = auto()
    EVERY_HOUR = auto()
    EVERY_DAY = auto()
    EVERY_WEEK = auto()
    
    CHAT_INTERACTION_INITIATED = auto()
    CHAT_CHANNEL_LLM = auto()
    
    BUSINESS_IDEA_SUBMITTED = auto()
    BUSINESS_JOB_DESCRIPTIONS_REQUESTED = auto()
    FIND_NICHE_BUSINESS_IDEAS = auto()
    
    # Autonomous agent message types
    ANALYSIS_REQUESTED = auto()
    ANALYSIS_COMPLETED = auto()
    ANALYSIS_ADDED = auto()
    BOARD_GUIDANCE_REQUESTED = auto()
    BOARD_GUIDANCE_UPDATED = auto()
    BUSINESS_GUIDANCE_UPDATED = auto()
    
    BUSINESS_BOOTSTRAP_REQUESTED = auto()
    BUSINESS_SECOND_OPINION_EVALUATION_REQUESTED = auto()
    
    PORTFOLIO_ADD_BUSINESSES_REQUESTED = auto()
    PORTFOLIO_REDUCE_BUSINESSES_REQUESTED = auto()
    PORTFOLIO_PICK_NEW_BUSINESS = auto()
    RESOURCE_PLANNING_REQUESTED = auto()
    TASK_ASSIGNED = auto()
    TASK_UPDATED = auto()
    TASK_BLOCKED = auto()
    TASK_COMPLETED = auto()
    TASK_FAILED = auto()
    TASK_SPEND = auto()
    CEO_DIRECTIVES_ISSUED = auto()
    PRODUCT_INITIATIVES_REQUESTED = auto()
    PRODUCT_INITIATIVES_DEFINED = auto()
    INITIATIVE_DEFINITION_REQUESTED = auto()
    INITIATIVE_DEFINED = auto()
    INITIATIVE_GREEN_LIT = auto()
    DEPLOYMENT_WORK_REQUESTED = auto()
    CODING_WORK_REQUESTED = auto()
    DESIGN_WORK_REQUESTED = auto()
    HUMAN_WORK_REQUESTED = auto()
    ENGINEERING_PLANNING_REQUESTED = auto()
    BUSINESS_ARCHITECTURE_GENERATION_REQUESTED = auto()
    PRODUCT_PLANNING_REQUESTED = auto()
    INITIATIVE_DEPLOY_REQUESTED = auto()
    INITIATIVE_PLANNING_REQUESTED = auto()
    ITERATION_PLANNING_REQUESTED = auto()
    ITERATION_WORK_REQUESTED = auto()
    SELF_DRIVING_CODER_REQUESTED = auto()
    SELF_DRIVING_CODER_UPDATED = auto()
    SELF_DRIVING_CODER_COMPLETED = auto()
    
    LLM_REQUEST = auto()
    
    RESET_TASK_TEST = auto()
    INITIATIVE_ARCHITECTURE_GENERATION_REQUESTED = auto()
    INITIATIVE_TASKS_GENERATION_REQUESTED = auto()
    INITIATIVE_USER_DOCUMENTATION_GENERATION_REQUESTED = auto()
    
    @staticmethod
    def get_cuda_only_message_types() -> Tuple['PubSubMessageType']:
        return {
        }
    
    def get_namespace(self, namespace_id):
        if namespace_id is None:
            return self.__class__.__name__
        
        return f"{self}_{namespace_id}"


class Constants(ErieEnum):
    NEW_BUSINESS_NAME_PREFIX = "New Business Idea"


class ScaleAction(ErieEnum):
    INCREASE = auto()
    NO_CHANGE = auto()
    DECREASE = auto()
    GO_TO_ZERO = auto()


class AutoScalingGroup(ErieEnum):
    MESSAGE_PROCESSOR = auto()
    WEBSERVICE = auto()


class PersonAuthStatus(ErieEnum):
    NOT_LOGGED_IN = auto()
    BANNED = auto()
    NOT_INVITED = auto()
    INVITED_BUT_NOT_CONFIRMED = auto()
    ALL_GOOD = auto()


class DataSplit(ErieEnum):
    TRAIN = auto()
    TEST = auto()
    VALIDATE = auto()


class ReportingPeriod(ErieEnum):
    WEEK = "7"
    THIRTY_DAYS = "30"
    NINETY_DAYS = "90"
    
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
    ML_ENGINEER = auto()
    
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


class LlmCreativity(ErieEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class LlmReasoningEffort(ErieEnum):
    NONE = "none"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class LlmVerbosity(ErieEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class LlmModel(ErieEnum):
    OPENAI_GPT_5_1 = "gpt-5.1"
    OPENAI_GPT_5 = "gpt-5"
    OPENAI_GPT_5_MINI = "gpt-5-mini"
    OPENAI_GPT_5_NANO = "gpt-5-nano"
    OPENAI_GPT_3_5_TURBO = "gpt-3.5-turbo"
    OPENAI_GPT_4_1 = "gpt-4.1"
    OPENAI_GPT_4_1_MINI = "gpt-4.1-mini"
    OPENAI_GPT_4_1_NANO = "gpt-4.1-nano"
    OPENAI_GPT_4_TURBO = "gpt-4-turbo"
    OPENAI_GPT_4o = "gpt-4o"
    OPENAI_GPT_4o_20240806 = "gpt-4o-2024-08-06"
    OPENAI_O3_MINI = "o3-mini-2025-01-31"
    OPENAI_O3_PRO = "o3-pro-2025-06-10"
    OPENAI_O1 = "o1"
    OPENAI_O1_MINI = "o1-mini"
    OPENAI_O3 = "o3"
    OPENAI_O4 = "o4"
    OPENAI_O4_MINI = "o4-mini"
    
    GEMINI_3_0_PRO = "gemini-3-pro-preview"
    GEMINI_3_0_FLASH = "gemini-3-flash-preview"
    GEMINI_2_5_PRO = "gemini-2.5-pro-preview-03-25"
    GEMINI_2_0_FLASH = "gemini-2.0-flash"
    
    CLAUDE_4_5 = "claude-sonnet-4-5-20250929"
    CLAUDE_3_7 = "claude-3-7-sonnet-20250219"
    CLAUDE_3_5 = "claude-3-5-sonnet-20240620"
    CLAUDE_3_OPUS = "claude-3-opus-20240229"
    
    DEEPSEEK_CODER = "deepseek-coder"
    DEEPSEEK_CHAT = "deepseek-chat"
    
    def get_input_price(self, content):
        from erieiron_common.llm_apis.llm_constants import get_token_count, MODEL_PRICE_USD_PER_MILLION_TOKENS
        price_per_million_tokens = MODEL_PRICE_USD_PER_MILLION_TOKENS[self]["input"]
        token_count = get_token_count(self, content)
        
        token_count = token_count or 0
        msg_cost = (token_count / 1_000_000.0) * float(price_per_million_tokens)
        
        return round(msg_cost, 6)


class S3Bucket(ErieEnum):
    MODELS = auto()


class ConsentChoice(ErieEnum):
    ACCEPTED = auto()
    DECLINED = auto()
    
    @staticmethod
    def from_bool(bool_val) -> "ConsentChoice":
        from erieiron_common import common
        return ConsentChoice.ACCEPTED if common.parse_bool(bool_val) else ConsentChoice.DECLINED


class Level(ErieEnum):
    HIGH = "100"
    MEDIUM = "050"
    LOW = "000"
    
    @classmethod
    def valid_or(cls, value, default_val=None):
        value = (value or "").upper()
        if value == Level.HIGH.name:
            return Level.HIGH
        elif value == Level.MEDIUM.name:
            return Level.MEDIUM
        elif value == Level.LOW.name:
            return Level.LOW
        else:
            return cls.valid_or(value, default_val)


class BusinessIdeaSource(ErieEnum):
    HUMAN = auto()
    BUSINESS_FINDER_AGENT = auto()


class CapabilityStatus(ErieEnum):
    # obsolete
    PENDING = auto()
    pass


class TaskExecutionType(ErieEnum):
    RUN = auto()
    MONITOR = auto()
    VALIDATE = auto()
    DEPLOY = auto()


class TaskExecutionSchedule(ErieEnum):
    NOT_APPLICABLE = auto()
    ONCE = auto()
    DAEMON = auto()
    HOURLY = auto()
    DAILY = auto()
    WEEKLY = auto()


class TaskType(ErieEnum):
    BOOTSRAP_CLONE_REPO = auto()
    PRODUCTION_DEPLOYMENT = auto()
    INITIATIVE_VERIFICATION = auto()
    CODING_APPLICATION = auto()
    CODING_ML = auto()
    TASK_EXECUTION = auto()
    DESIGN_WEB_APPLICATION = auto()
    HUMAN_WORK = auto()


class LlcStructure(ErieEnum):
    ERIE_IRON_LLC = auto()
    SEPARATE_LLC = auto()


class InitiativeType(ErieEnum):
    PRODUCT = auto()
    ENGINEERING = auto()
    SALES = auto()


class EnvironmentType(ErieEnum):
    DEV = "dev"
    PRODUCTION = "production"
    
    def get_aws_region(self) -> str:
        return "us-west-2"


class CloudProvider(ErieEnum):
    AWS = "aws"
    GCP = "gcp"
    AZURE = "azure"


class FailureClassification(ErieEnum):
    SYNTAX_ERROR = auto()
    IMPORT_ERROR = auto()
    VERSION_MISMATCH = auto()
    ATTRIBUTE_ERROR = auto()
    MISSING_DEPENDENCY = auto()
    CONFIGURATION_ERROR = auto()
    NETWORK_ERROR = auto()
    UNKNOWN = auto()


class CredentialServiceProvisioning(ErieEnum):
    USER_SUPPLIED = auto()
    STACK_GENERATED = auto()


class CredentialService(ErieEnum):
    RDS = auto()
    COGNITO = auto()
    LLM = auto()
    DJANGO = auto()
    STRIPE = auto()
    HCAPTCHA = auto()
    ONESIGNAL = auto()
    OAUTH_APPLE = auto()
    FIREBASE_FCM = auto()
    OAUTH_GITHUB = auto()
    OAUTH_GOOGLE = auto()
    COINBASE_COMMERCE = auto()
    
    def get_spec(self) -> dict:
        from erieiron_autonomous_agent.coding_agents.credential_manager import CREDENTIAL_DEFINITIONS
        return CREDENTIAL_DEFINITIONS.get(self)
    
    def get_label(self) -> str:
        return self.get_spec().get(
            "runtime_env_var"
        ).replace(
            "_SECRET_ARN", ""
        ).replace(
            "_", " "
        ).title()
    
    def get_desc(self) -> list[dict]:
        return self.get_spec().get("description")
    
    def get_secret_schema(self) -> list[dict]:
        return self.get_spec().get("secret_value_schema")
    
    def get_provisioning(self) -> CredentialServiceProvisioning:
        return CredentialServiceProvisioning(self.get_spec().get("provisioning"))
    
    def get_stackoutput_var(self) -> tuple[str, str]:
        return self.get_spec().get("stack_var")
    
    def get_stackoutput_and_env_names(self) -> tuple[str, str]:
        spec = self.get_spec()
        return spec.get("stack_var"), spec.get("runtime_env_var")
    
    def get_secret_keys(self) -> list[str]:
        return [s.get("key") for s in self.get_secret_schema()]


class ContainerPlatform(ErieEnum):
    FARGATE = "linux/amd64"
    LAMBDA = "linux/arm64"


class CloudformationResourceType(ErieEnum):
    S3_BUCKET = "AWS::S3::Bucket"
    LAMBDA_FUNCTION = "AWS::Lambda::Function"
    ELB_LOADBALANCER = "AWS::ElasticLoadBalancingV2::LoadBalancer"


class InfrastructureStackType(ErieEnum):
    FOUNDATION = auto()
    APPLICATION = auto()
    TARGET_ACCOUNT_BOOTSTRAP = auto()
    
    def get_opentofu_config(self) -> str:
        if InfrastructureStackType.FOUNDATION.eq(self):
            logging.warning(f"{InfrastructureStackType.FOUNDATION} is deprecated")
            return "./opentofu/foundation/stack.tf"
        elif InfrastructureStackType.APPLICATION.eq(self):
            return "./opentofu/application/stack.tf"
        elif InfrastructureStackType.TARGET_ACCOUNT_BOOTSTRAP.eq(self):
            return "./opentofu/target_account_provisioning/stack.tf"
        else:
            raise Exception(f"unhandled stack type {self}")


class StackStrategy(ErieEnum):
    """
    Defines how a business manages infrastructure stacks.

    - SINGLE_STACK: All work in production stack only (minimal isolation, lowest cost)
    - PROD_AND_DEV: Two stacks - production and shared development
    - PER_INITIATIVE: Production + one stack per initiative (maximum isolation, current behavior)
    """
    SINGLE_STACK = 'single_stack'
    PROD_AND_DEV = 'prod_and_dev'
    PER_INITIATIVE = 'per_initiative'
    
    def allows_dev_stack(self) -> bool:
        """Returns True if this strategy allows a business-level dev stack."""
        return self == StackStrategy.PROD_AND_DEV
    
    def allows_initiative_stacks(self) -> bool:
        """Returns True if this strategy allows initiative-scoped stacks."""
        return self == StackStrategy.PER_INITIATIVE
    
    def requires_production_only(self) -> bool:
        """Returns True if all work must happen in production stack."""
        return self == StackStrategy.SINGLE_STACK


class InitiativeNames(ErieEnum):
    OPERATIONAL_TASKS = "Operational Tasks"
    BOOTSTRAP_ENVS = "Bootstrap Business"
    BOOTSTRAP_ENVS_LEGACY = "BOOTSTRAP_ENVS"



class PublicPrivate(ErieEnum):
    PUBLIC = auto()
    PRIVATE = auto()


class SdaPhase(ErieEnum):
    INIT = auto()
    PLANNING = auto()
    CODING = auto()
    BUILD = auto()
    DEPLOY = auto()
    EXECUTION = auto()
    EVALUATE = auto()


class BusinessNiche(ErieEnum):
    API_INTEGRATION_SERVICES = auto()
    B2B_PROCESS_AUTOMATION = auto()
    ECOMMERCE_AUTOMATION = auto()
    LEAD_QUALIFICATION_AUTOMATION = auto()
    LOCAL_SERVICE_ARBITRAGE = auto()
    PROFESSIONAL_SERVICES = auto()
    SUBSCRIPTION_MANAGEMENT_AUTOMATION = auto()
    
    def get_prompt_filename(self) -> str:
        return f"niche_finders/{str(self.value).lower()}.md"


class CredentialsSpace(ErieEnum):
    ERIE_IRON = auto()
    TARGET_ACCOUNT = auto()


class OpenTofuErrorType(ErieEnum):
    DUPLICATE_IDEMPOTENT = "duplicate_idempotent"
    HARD_ERROR_WITH_DUPLICATE = "hard_error_with_duplicate"
    MISSING_PERMISSIONS = "missing_permissions"
    TRANSIENT_ERROR = "transient_error"
    PERMANENT_ERROR = "permanent_error"
    SECRET_NEEDS_RESTORATION = "secret_needs_restoration"


class TaskImplementationPhase(ErieEnum):
    UI_MOCK_API = auto()
    SERVER_IMPLEMENTATION = auto()


class BuildStep(ErieEnum):
    BUILD_CONTAINER = auto()
    BUILD_LAMBDAS = auto()
    RUN_BACKEND_TESTS = auto()
    RUN_FRONTEND_TESTS = auto()
    RUN_TESTS_IN_CONTAINER = auto()
    RUN_TESTS_LOCALLY = auto()
    MIGRATE_DATABASE = auto()
    PUSH_TO_ECR = auto()
    UPDATE_STACK_TF = auto()
    DEPLOY_TO_AWS = auto()


class IterationMode(ErieEnum):
    """
    Tracks the execution stage within a coding task iteration.
    Determines whether tests run locally, in container, or require AWS deployment.
    """
    LOCAL_TESTS = auto()  # Run tests locally in venv (fast iteration)
    CONTAINER_TESTS = auto()  # Run tests in built container (pre-deployment verification)
    AWS_DEPLOYMENT = auto()  # Full deployment with ECR push and AWS stack update


ERIE_IRON = auto()
