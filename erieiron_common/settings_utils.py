import json
import logging
import os
from pathlib import Path

import decouple

from erieiron_common.local_runtime import DEFAULT_LOCAL_CONFIG_PATH, resolve_local_runtime_path
from erieiron_common import secret_utils


def get_logging(debug_sql_statements=False):
    conf = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'standard': {
                'format': '%(asctime)s.%(msecs)03d %(levelname)s %(message)s',
                'datefmt': '%Y-%m-%d %H:%M:%S'
            }
        },
        'filters': {
            'skip_disallowed_host': {
                '()': 'django.utils.log.CallbackFilter',
                'callback': lambda record: 'Invalid HTTP_HOST header' not in record.getMessage()
            },
        },
        'handlers': {
            'console': {
                'level': 'INFO',
                'class': 'logging.StreamHandler',
                'formatter': 'standard',
                'stream': 'ext://sys.stdout',
                'filters': ['skip_disallowed_host'],
            },
            'null': {
                'class': 'logging.NullHandler',
            },
        },
        'loggers': {
            '': {'handlers': ['console'], 'level': 'INFO'},
            "openai": {"handlers": ["console"], "level": "WARNING", "propagate": False},
            "httpx": {"handlers": ["console"], "level": "WARNING", "propagate": False},
            "httpcore": {"handlers": ["console"], "level": "WARNING", "propagate": False},
            'django.security.DisallowedHost': {'handlers': ['null'], 'propagate': False},
            'django.security.csrf': {'handlers': ['null'], 'level': 'ERROR', 'propagate': False},
        },
    }
    
    if debug_sql_statements:
        debug_conf = {
            'formatters': {
                'sql': {
                    'format': '%(asctime)s %(duration).3f ms %(message)s',
                }
            },
            'handlers': {
                'console_sql': {
                    'level': 'DEBUG',
                    'class': 'logging.StreamHandler',
                    'formatter': 'sql',
                }
            },
            'loggers': {
                'django.db.backends': {
                    'handlers': ['console_sql'],
                    'level': 'DEBUG',
                    'propagate': False,
                }
            }
        }
        for section, settings in debug_conf.items():
            conf[section].update(settings)
    
    return conf


def get_secret_key(config):
    try:
        return get_secret("DJANGO_SECRET_KEY")["DJANGO_SECRET_KEY"]
    except Exception as e:
        logging.exception(e)
        return 'django-insecure-5xxn9-j8i06-ejwaw3^k!ld8@kvvuoa773_6+3+)f6sy3j4_g$'


def get_secret(secret_name: str):
    return secret_utils.get_secret(secret_name)


def get_buckets(config):
    return {
    }


def get_platform_config_path() -> Path:
    return resolve_local_runtime_path(
        os.getenv("ERIEIRON_LOCAL_CONFIG_FILE"),
        DEFAULT_LOCAL_CONFIG_PATH,
    )


def get_platform_config_example_path(config_path: Path | None = None) -> Path:
    resolved_path = config_path or get_platform_config_path()
    return resolved_path.with_name(
        f"{resolved_path.stem}.example{resolved_path.suffix}"
    )


def load_platform_config_payload() -> dict:
    config_path = get_platform_config_path()
    config_payload = {}

    for candidate_path in [get_platform_config_example_path(config_path), config_path]:
        if not candidate_path.exists():
            continue

        candidate_payload = json.loads(candidate_path.read_text(encoding="utf-8"))
        if not isinstance(candidate_payload, dict):
            raise ValueError(f"platform config file must be a JSON object: {candidate_path}")

        config_payload.update(candidate_payload)

    return config_payload


def stringify_env_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return str(value)


def get_platform_environment_overrides() -> dict[str, str]:
    config_payload = load_platform_config_payload()
    return {
        key: stringify_env_value(value)
        for key, value in config_payload.items()
        if value is not None and not isinstance(value, dict)
    }


def sync_platform_config_to_env():
    for env_key, value in get_platform_environment_overrides().items():
        if os.environ.get(env_key) in (None, ""):
            os.environ[env_key] = value


def get_config():
    sync_platform_config_to_env()
    return decouple.Config(decouple.RepositoryEmpty())


def get_optional_secret_value(secret_name: str, field_name: str, default_value=""):
    try:
        secret_payload = get_secret(secret_name)
    except Exception:
        return default_value

    return stringify_env_value(secret_payload.get(field_name, default_value))


def sync_config_to_env(config, env_mappings: list[tuple[str, str]]):
    for config_key, env_key in env_mappings:
        value = config.get(config_key, default=None)
        if value in (None, ""):
            continue
        if os.environ.get(env_key) in (None, ""):
            os.environ[env_key] = str(value)


def default_str(s, default_val=""):
    if s is None:
        return default_val
    
    s = str(s)
    if not s:
        return default_val
    else:
        return s
