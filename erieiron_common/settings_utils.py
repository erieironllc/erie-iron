import base64
import json
import logging
import os
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


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
    # Create a Secrets Manager client
    session = boto3.session.Session()
    client = session.client(
        service_name='secretsmanager',
        region_name=os.getenv("AWS_REGION", "us-west-2")
    )
    
    try:
        get_secret_value_response = client.get_secret_value(
            SecretId=secret_name
        )
    except ClientError as e:
        logging.exception(e)
        raise e
    else:
        # Decrypts secret using the associated KMS CMK
        # Depending on whether the secret is a string or binary, one of these fields will be populated
        if 'SecretString' in get_secret_value_response:
            secret = get_secret_value_response['SecretString']
            return json.loads(secret)
        else:
            decoded_binary_secret = base64.b64decode(get_secret_value_response['SecretBinary'])
            return json.loads(decoded_binary_secret)


def get_buckets(config):
    return {
    }


def get_config():
    import decouple
    
    # Get the path from the ERIEIRON_ENV environment variable
    erieiron_env = os.getenv('ERIEIRON_ENV')  # Default to '.env' if ERIEIRON_ENV is not set
    erieiron_env_commandline = os.getenv('ERIEIRON_ENV_COMMANDLINE')
    if erieiron_env_commandline:
        erieiron_env = erieiron_env_commandline
    
    if erieiron_env is None:
        raise Exception("ERIEIRON_ENV is not defined")
    
    conf_file = Path(os.getcwd()) / 'conf' / f'./.env.{erieiron_env}'
    config = decouple.Config(decouple.RepositoryEnv(conf_file))
    print("config file", conf_file)
    
    return config


def default_str(s, default_val=""):
    if s is None:
        return default_val
    
    s = str(s)
    if not s:
        return default_val
    else:
        return s
