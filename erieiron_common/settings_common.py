import tempfile

from erieiron_common import settings_utils

config = settings_utils.get_config()
DEBUG = config.get("DEBUG", True)

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
