import logging
import logging.config
import os
import tempfile
from pathlib import Path

from erieiron_common import settings_utils

os.environ["TOKENIZERS_PARALLELISM"] = "false"

DEBUG = True

BASE_DIR = Path(__file__).resolve().parent
TIME_ZONE = 'America/Los_Angeles'
STATIC_ROOT = os.path.join(Path(__file__).resolve().parent, "erieiron_ui", "static")
SECRET_KEY = "django-insecure-4yp%)5s=rx5ph(+zs7mhk&zj9&sko+15(bi=nx-94^m-hrd&2v"
AWS_ACCOUNT_ID = "782005355493"

BUSINESS_SANDBOX_ROOTDIR = Path("./erieiron_businesses")

ALLOWED_HOSTS = []

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
