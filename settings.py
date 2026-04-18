import logging
import logging.config
import os
import tempfile
from pathlib import Path

from decouple import Csv

from erieiron_common import settings_utils
from erieiron_public import agent_tools

os.environ["TOKENIZERS_PARALLELISM"] = "false"

BASE_DIR = Path(__file__).resolve().parent
config = settings_utils.get_config()
settings_utils.sync_config_to_env(
    config,
    [
        ("ERIEIRON_RUNTIME_PROFILE", "ERIEIRON_RUNTIME_PROFILE"),
        ("ERIEIRON_LOCAL_SECRETS_FILE", "ERIEIRON_LOCAL_SECRETS_FILE"),
        ("LOCAL_DB_NAME", "LOCAL_DB_NAME"),
        ("ERIEIRON_DB_HOST", "ERIEIRON_DB_HOST"),
        ("ERIEIRON_DB_NAME", "ERIEIRON_DB_NAME"),
        ("ERIEIRON_DB_PORT", "ERIEIRON_DB_PORT"),
        ("RDS_SECRET_ARN", "RDS_SECRET_ARN"),
        ("AWS_DEFAULT_REGION_NAME", "AWS_DEFAULT_REGION"),
        ("AWS_DEFAULT_REGION_NAME", "AWS_DEFAULT_REGION_NAME"),
        ("COGNITO_SECRET_ARN", "COGNITO_SECRET_ARN"),
        ("COGNITO_USER_POOL_ID", "COGNITO_USER_POOL_ID"),
        ("COGNITO_CLIENT_ID", "COGNITO_CLIENT_ID"),
        ("COGNITO_DOMAIN", "COGNITO_DOMAIN"),
        ("LOCAL_AUTH_ENABLED", "LOCAL_AUTH_ENABLED"),
        ("LOCAL_AUTH_EMAIL", "LOCAL_AUTH_EMAIL"),
        ("LOCAL_AUTH_PASSWORD", "LOCAL_AUTH_PASSWORD"),
        ("LOCAL_AUTH_NAME", "LOCAL_AUTH_NAME"),
    ],
)
DEBUG = str(config.get("DEBUG", "True")).lower().strip() == "true"
ERIEIRON_RUNTIME_PROFILE = config.get(
    "ERIEIRON_RUNTIME_PROFILE",
    "local" if os.getenv("ERIEIRON_ENV", "").strip().lower() == "dev_local" else "aws",
)
ERIEIRON_LOCAL_SECRETS_FILE = config.get("ERIEIRON_LOCAL_SECRETS_FILE", "conf/local_secrets.json")
LOCAL_AUTH_ENABLED = config(
    "LOCAL_AUTH_ENABLED",
    default=ERIEIRON_RUNTIME_PROFILE == "local",
    cast=bool,
)
LOCAL_AUTH_EMAIL = config("LOCAL_AUTH_EMAIL", default="local-admin@erieiron.local", cast=str)
LOCAL_AUTH_PASSWORD = config("LOCAL_AUTH_PASSWORD", default="", cast=str)
LOCAL_AUTH_NAME = config("LOCAL_AUTH_NAME", default="Local Admin", cast=str)

VALIDATION_PORT = 8006
TIME_ZONE = 'America/Los_Angeles'
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

DISABLE_EMAIL_SEND = config('DISABLE_EMAIL_SEND', default=False, cast=bool)
CLIENT_MESSAGE_WEBSOCKET_ENDPOINT = os.getenv('CLIENT_MESSAGE_WEBSOCKET_ENDPOINT')
CLIENT_MESSAGE_DYNAMO_TABLE = os.getenv('CLIENT_MESSAGE_DYNAMO_TABLE')
GOOGLE_ANALYTICS_PROPERTY_ID = "TODO"
MESSAGE_QUEUE_ENV = config('MESSAGE_QUEUE_ENV', default=None, cast=str)
MESSAGE_TYPES = os.getenv("MESSAGE_TYPES", config('MESSAGE_TYPES', default=None, cast=str))
BUCKETS = settings_utils.get_buckets(config)

# DEPRECATED: Simple auth replaced by Cognito
# SIMPLE_AUTH_ALLOWED_EMAIL = os.getenv("SIMPLE_AUTH_ALLOWED_EMAIL", "jj@erieironllc.com,sach.nanda@gmail.com")
# SIMPLE_AUTH_ALLOWED_PASSWORD = os.getenv("SIMPLE_AUTH_ALLOWED_PASSWORD", "change_th1s_p@ssword")
# SIMPLE_AUTH_COOKIE_NAME = "erieiron_ui_auth_token"
# SIMPLE_AUTH_JWT_SECRET = SECRET_KEY
# SIMPLE_AUTH_TOKEN_TTL_SECONDS = 12 * 60 * 60  # 12 hours
# SIMPLE_AUTH_LOGIN_URL = "/login/"
# SIMPLE_AUTH_LOGOUT_URL = "/logout/"

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
    "erieironllc.com",
]
if ERIEIRON_RUNTIME_PROFILE == "local":
    ALLOWED_HOSTS.append("host.docker.internal")

CSRF_TRUSTED_ORIGINS = config(
    'CSRF_TRUSTED_ORIGINS',
    default="https://erieironllc.com,https://*.erieironllc.com",
    cast=Csv()
)

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
    'rest_framework',
    'corsheaders',
]

MIDDLEWARE = [
    "erieiron_ui.middleware.HealthCheckBypassMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "erieiron_ui.middleware.CognitoAuthMiddleware",  # Replaced SimpleAuthMiddleware
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

if ERIEIRON_RUNTIME_PROFILE == "local":
    local_db_name = os.getenv("LOCAL_DB_NAME", "erieiron_v1")
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": local_db_name,
            "HOST": "localhost",
            "PORT": "5432",
            "TEST": {
                "NAME": local_db_name,
            },
        }
    }
else:
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

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

_static_compiled_path = Path(STATIC_COMPILED_DIR)
if not _static_compiled_path.is_absolute():
    _static_compiled_path = (BASE_DIR / _static_compiled_path).resolve()

STATICFILES_DIRS = []
if _static_compiled_path.resolve() != Path(STATIC_ROOT).resolve():
    _static_compiled_path.mkdir(parents=True, exist_ok=True)
    STATICFILES_DIRS.append(_static_compiled_path)

# Default primary key field type
# https://docs.djangoproject.com/en/5.1/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Cognito Configuration
COGNITO_SECRET_ARN = os.getenv('COGNITO_SECRET_ARN')

# Django REST Framework
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
}

# Simple JWT Configuration
from datetime import timedelta

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=30),
    'ROTATE_REFRESH_TOKENS': False,
    'BLACKLIST_AFTER_ROTATION': False,
    'UPDATE_LAST_LOGIN': True,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_HEADER_NAME': 'HTTP_AUTHORIZATION',
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
}

# CORS Configuration
CORS_ALLOWED_ORIGINS = [
    origin
    for origin in [f'https://{os.getenv("DOMAIN_NAME")}']
    if origin != "https://None"
]

if DEBUG:
    CORS_ALLOWED_ORIGINS += [
        BASE_URL.rstrip("/"),
        'http://localhost:8000',
        'http://127.0.0.1:8000',
        'http://localhost:8024',
        'http://127.0.0.1:8024',
    ]
CORS_ALLOWED_ORIGINS = list(dict.fromkeys(CORS_ALLOWED_ORIGINS))

# HTTPS Enforcement (Production only)
if not DEBUG:
    # SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True

LOGGING = settings_utils.get_logging(debug_sql_statements=False)
logging.config.dictConfig(LOGGING)
