import logging
import logging.config
import os
import tempfile
from pathlib import Path

from erieiron_common import settings_utils
from erieiron_public import agent_tools

os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["TREE_SITTER_SKIP_VENDOR"] = "1"

BASE_DIR = Path(__file__).resolve().parent
config = settings_utils.get_config()
DEBUG = config.get("DEBUG", True)

VALIDATION_PORT = 8006
TIME_ZONE = 'America/Los_Angeles'
STATIC_ROOT = os.path.join(Path(__file__).resolve().parent, "erieiron_ui", "static")
SECRET_KEY = "django-insecure-4yp%)5s=rx5ph(+zs7mhk&zj9&sko+15(bi=nx-94^m-hrd&2v"
LLM_API_KEYS_SECRET_ARN = "arn:aws:secretsmanager:us-west-2:782005355493:secret:LLM_API_KEYS-B1Bn3t"
STRIPE_WEBHOOK_SECRET_ARN = "TODO"
SELF_DRIVING_IAC_PROVIDER = os.getenv("SELF_DRIVING_IAC_PROVIDER", "opentofu").lower()
TOFU_BIN = os.environ.get("OPENTOFU_BIN", "tofu")

BASE_URL = config.get("BASE_URL", "http://localhost:8000")
STATIC_COMPILED_DIR = config('STATIC_COMPILED_DIR')
REQUIRED_ACCOUNT_NAME = config('REQUIRED_ACCOUNT_NAME', default="Erie Iron LLC", cast=str)
ALLOW_MPS_DEVICE = config('ALLOW_MPS_DEVICE', default=False, cast=bool)
AWS_ACCOUNT_ID = config("AWS_ACCOUNT_ID")
AWS_DEFAULT_REGION_NAME = config("AWS_DEFAULT_REGION_NAME")
CLOUDFRONT_DOMAIN_AUDIO = config('CLOUDFRONT_DOMAIN_AUDIO', default="todo")
CLOUDFRONT_DOMAIN_WAVEFORM = config('CLOUDFRONT_DOMAIN_WAVEFORM', default="todo")
CLOUDFRONT_KEY_PAIR_ID = config('CLOUDFRONT_KEY_PAIR_ID', default="todo")
CLOUDFRONT_PRIVATE_KEY_SECRET_NAME = config('CLOUDFRONT_PRIVATE_KEY_SECRET_NAME', default="cloudfront-urlsigning-private-key")
SYSTEM_ACCOUNT_EMAIL = config('SYSTEM_ACCOUNT_EMAIL', default="erieironllc@gmail.com", cast=str)
FEEDBACK_EMAIL = config('FEEDBACK_EMAIL', default="erieironllc@gmail.com", cast=str)
START_MESSAGE_QUEUE_PROCESSOR = config('START_MESSAGE_QUEUE_PROCESSOR', default=False, cast=bool)
RUNTIME_CONFIG_OVERRIDES = config("RUNTIME_CONFIG_OVERRIDES", default=None)
SHOW_TIMERS = config('SHOW_TIMERS', default=False, cast=bool)

S3_CACHE_DIR = config('S3_CACHE_DIR', default=tempfile.mkdtemp(), cast=str)
S3_CACHE_MAX_DISK_USAGE = config('S3_CACHE_MAX_DISK_USAGE', default=70, cast=int)
HF_MODEL_CACHE_S3_URI = config('HF_MODEL_CACHE_S3_URI', default=None)

COGNITO_USER_POOL_ID = config("COGNITO_USER_POOL_ID")
COGNITO_CLIENT_ID = config("COGNITO_CLIENT_ID")
COGNITO_DOMAIN = config("COGNITO_DOMAIN", default="https://login.collaya.com")

DISABLE_EMAIL_SEND = config('DISABLE_EMAIL_SEND', default=False, cast=bool)
CLIENT_MESSAGE_WEBSOCKET_ENDPOINT = "rkbq6d3yd4.execute-api.us-west-2.amazonaws.com/production"
CLIENT_MESSAGE_DYNAMO_TABLE = "client-websocket_connections-db"
GOOGLE_ANALYTICS_PROPERTY_ID = "TODO"
MESSAGE_QUEUE_ENV = config('MESSAGE_QUEUE_ENV', default=None, cast=str)
MESSAGE_TYPES = config('MESSAGE_TYPES', default=None, cast=str)
BUCKETS = settings_utils.get_buckets(config)


SIMPLE_AUTH_ALLOWED_EMAIL = os.getenv("SIMPLE_AUTH_ALLOWED_EMAIL", "jj@erieironllc.com")
SIMPLE_AUTH_ALLOWED_PASSWORD = os.getenv("SIMPLE_AUTH_ALLOWED_PASSWORD", "change_th1s_p@ssword")
SIMPLE_AUTH_COOKIE_NAME = "erieiron_ui_auth_token"
SIMPLE_AUTH_JWT_SECRET = SECRET_KEY
SIMPLE_AUTH_TOKEN_TTL_SECONDS = 12 * 60 * 60  # 12 hours
SIMPLE_AUTH_LOGIN_URL = "/login/"
SIMPLE_AUTH_LOGOUT_URL = "/logout/"

DOMAIN_CONTACT_INFO = {
    "FirstName": "ErieIron",
    "LastName": "LLC",
    "ContactType": "COMPANY",
    "OrganizationName": "Erie Iron LLC",
    "AddressLine1": "2108 N St. STE N",
    "City": "Sacramento",
    "State": "CA",
    "CountryCode": "US",
    "ZipCode": "95816",
    "PhoneNumber": "+1.9253381985",
    "Email": "erieironllc@gmail.com"
}

READONLY_FILES = [
    {
        "path": "manage.py",
        "alternatives": "settings.py",
        "description": "Django's core management script"
    },
    {
        "path": "Dockerfile",
        "alternatives": "return blocked or otherwise ESCALATE_TO_HUMAN",
        "description": "the Dockerfile"
    },
    {
        "path": "docker-internal-startup-cmd.sh",
        "alternatives": "return blocked or otherwise ESCALATE_TO_HUMAN",
        "description": "the docker startup script"
    }
]

BUSINESS_SANDBOX_ROOTDIR = Path("./erieiron_businesses")

ALLOWED_HOSTS = [
    "127.0.0.1",
    "localhost",
    "erieironllc.com"
]

INSTALLED_APPS = [
    'erieiron_common.apps.ErieironCommonConfig',
    'erieiron_autonomous_agent.apps.ErieironAutonomousAgentConfig',
    'erieiron_ui',
    "django.contrib.admin",
    "django.contrib.auth",
    'django.contrib.humanize',
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

MIDDLEWARE = [
    "erieiron_ui.middleware.HealthCheckBypassMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "erieiron_ui.middleware.SimpleAuthMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "erieiron_config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [
            'erieiron_ui/templates',
        ],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "erieiron_config.wsgi.application"


DATABASES = agent_tools.get_django_settings_databases_conf()

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Internationalization
# https://docs.djangoproject.com/en/5.1/topics/i18n/

LANGUAGE_CODE = "en-us"

USE_I18N = True

USE_TZ = True

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.1/howto/static-files/

STATIC_URL = "static/"

# Default primary key field type
# https://docs.djangoproject.com/en/5.1/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = settings_utils.get_logging(debug_sql_statements=False)
logging.config.dictConfig(LOGGING)
