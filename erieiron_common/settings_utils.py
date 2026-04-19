import json
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

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


def _build_url_netloc(parsed_url, port: int | None) -> str:
    hostname = parsed_url.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"

    auth = ""
    if parsed_url.username:
        auth = parsed_url.username
        if parsed_url.password:
            auth = f"{auth}:{parsed_url.password}"
        auth = f"{auth}@"

    if port is None:
        return f"{auth}{hostname}"
    return f"{auth}{hostname}:{port}"


def set_url_port(url: str, port: int | None) -> str:
    normalized_url = str(url).rstrip("/")
    parsed_url = urlsplit(normalized_url)
    if not parsed_url.scheme or parsed_url.hostname is None:
        return normalized_url

    return urlunsplit(
        (
            parsed_url.scheme,
            _build_url_netloc(parsed_url, port),
            parsed_url.path.rstrip("/"),
            parsed_url.query,
            parsed_url.fragment,
        )
    ).rstrip("/")


def strip_url_port(url: str) -> str:
    return set_url_port(url, None)


def join_url_path(base_url: str, path: str = "") -> str:
    normalized_base_url = str(base_url).rstrip("/")
    normalized_path = str(path).lstrip("/")
    if not normalized_path:
        return normalized_base_url
    return f"{normalized_base_url}/{normalized_path}"


def apply_webapp_port_to_local_origins(origins: list[str], webapp_port: int | None) -> list[str]:
    normalized_origins = []
    for origin in origins:
        normalized_origin = str(origin).rstrip("/")
        parsed_origin = urlsplit(normalized_origin)
        if webapp_port is not None and parsed_origin.hostname in {"localhost", "127.0.0.1"}:
            normalized_origin = set_url_port(normalized_origin, webapp_port)
        normalized_origins.append(normalized_origin)
    return list(dict.fromkeys(normalized_origins))


def get_runserver_port(default_port: int) -> int:
    if len(sys.argv) < 2 or sys.argv[1] != "runserver":
        return default_port

    expects_value = False
    for arg in sys.argv[2:]:
        if expects_value:
            expects_value = False
            continue

        if arg in {"--settings", "--pythonpath", "--verbosity", "-s", "-p", "-v"}:
            expects_value = True
            continue

        if arg.startswith("--settings=") or arg.startswith("--pythonpath=") or arg.startswith("--verbosity="):
            continue

        if arg.startswith("-"):
            continue

        return int(arg.rsplit(":", 1)[-1])

    return default_port
