import logging.config
import logging
import tempfile
from pathlib import Path

from erieiron_common import settings_utils

BASE_DIR = Path(__file__).resolve().parent
DEBUG = True
SECRET_KEY = "django-insecure-4yp%)5s=rx5ph(+zs7mhk&zj9&sko+15(bi=nx-94^m-hrd&2v"

config = settings_utils.get_config(BASE_DIR)
REQUIRED_ACCOUNT_NAME = config('REQUIRED_ACCOUNT_NAME', default="Erie Iron LLC", cast=str)
ALLOW_MPS_DEVICE = config('ALLOW_MPS_DEVICE', default=False, cast=bool)
S3_CACHE_DIR = config('S3_CACHE_DIR', default=tempfile.mkdtemp(), cast=str)
S3_CACHE_MAX_DISK_USAGE = config('S3_CACHE_MAX_DISK_USAGE', default=70, cast=int)
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

DISABLE_EMAIL_SEND = config('DISABLE_EMAIL_SEND', default=False, cast=bool)
CLIENT_MESSAGE_WEBSOCKET_ENDPOINT = "rkbq6d3yd4.execute-api.us-west-2.amazonaws.com/production"
CLIENT_MESSAGE_DYNAMO_TABLE = "client-websocket_connections-db"
GOOGLE_ANALYTICS_PROPERTY_ID = "TODO"
MESSAGE_QUEUE_ENV = config('MESSAGE_QUEUE_ENV', default=None, cast=str)
MESSAGE_TYPES = config('MESSAGE_TYPES', default=None, cast=str)
BUCKETS = settings_utils.get_buckets(config)

BUSINESS_SANDBOX_ROOTDIR = Path("./erieiron_businesses")


ALLOWED_HOSTS = []


INSTALLED_APPS = [
    'erieiron_common',
    'erieiron_ui',
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "erieiron_config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
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

WSGI_APPLICATION = "config.wsgi.application"


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "erieiron",
        "HOST": "localhost",
        "PORT": "5432",
    }
}


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

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.1/howto/static-files/

STATIC_URL = "static/"

# Default primary key field type
# https://docs.djangoproject.com/en/5.1/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = settings_utils.get_logging(
    config("DEBUG_SQL", default=False, cast=bool)
)

logging.config.dictConfig(LOGGING)
