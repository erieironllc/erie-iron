import base64
import json
import logging
import os

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
            '': {  # 'root' logger
                'handlers': ['console'],
                'level': 'INFO',
            },
            'django.security.DisallowedHost': {
                'handlers': ['null'],
                'propagate': False,
            },
            'django.security.csrf': {
                'handlers': ['null'],
                'level': 'ERROR',
                'propagate': False,
            },
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


def get_databases(config):
    try:
        DATABASES_AWS_SECRET = config('DATABASES_AWS_SECRET', None)
        if DATABASES_AWS_SECRET is not None:
            from erieiron_common import aws_utils
            db_credentials = get_secret(DATABASES_AWS_SECRET)
            print(f"db is {db_credentials['dbInstanceIdentifier']} ")

            if not default_str(os.getenv('ERIEIRON_ENV')).startswith("prod") and db_credentials['dbInstanceIdentifier'] == 'erieiron-db':
                print("""

!! DANGER DANGER: NON-PROD CODE CONNECTING TO THE PRODUCTION RDS DATABASE !!  
If this is intentional, be very very careful. if unintentional, please connect to the dev db by changing 
the DATABASES_AWS_SECRET value in your .env.dev file to DATABASES_AWS_SECRET = "erieiron-db-dev-credentials"

                """)

            return {
                'default': {
                    'ENGINE': 'django.db.backends.postgresql',
                    'NAME': 'postgres',
                    'USER': db_credentials['username'],
                    'PASSWORD': db_credentials['password'],
                    'HOST': db_credentials['host'],
                    'PORT': db_credentials['port'],
                }
            }
        else:
            return {
                'default': {
                    'ENGINE': config('DATABASE_ENGINE'),
                    'NAME': config('DATABASE_NAME'),
                    'USER': config('DATABASE_USER', None),
                    'PASSWORD': config('DATABASE_PASSWORD', None),
                    'HOST': config('DATABASE_HOST', None),
                    'PORT': config('DATABASE_PORT', default=5432, cast=int)
                }
            }
    except Exception as e:
        logging.exception(e)
        return {
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': ':memory:',
            }
        }


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


def get_config(base_dir):
    import decouple

    # Get the path from the ERIEIRON_ENV environment variable
    erieiron_env = os.getenv('ERIEIRON_ENV')  # Default to '.env' if ERIEIRON_ENV is not set
    erieiron_env_commandline = os.getenv('ERIEIRON_ENV_COMMANDLINE')
    if erieiron_env_commandline:
        erieiron_env = erieiron_env_commandline

    if erieiron_env is None:
        raise Exception("ERIEIRON_ENV is not defined")

    try:
        config = decouple.Config(decouple.RepositoryEnv(os.path.join(base_dir, './conf/.env.%s' % erieiron_env)))
        logging.debug(" ".join(["ERIEIRON_ENV", erieiron_env, os.path.join(base_dir, './conf/.env.%s' % erieiron_env)]))
    except:
        config = decouple.Config(decouple.RepositoryEnv(os.path.join(base_dir, './.env.%s' % erieiron_env)))
        logging.debug(" ".join(["ERIEIRON_ENV", erieiron_env, os.path.join(base_dir, './.env.%s' % erieiron_env)]))

    return config


def default_str(s, default_val=""):
    if s is None:
        return default_val

    s = str(s)
    if not s:
        return default_val
    else:
        return s
