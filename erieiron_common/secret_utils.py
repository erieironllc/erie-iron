import base64
import json
import os

import boto3
from botocore.exceptions import ClientError

from erieiron_common.local_runtime import get_local_secrets_path, is_local_runtime


def get_secret(secret_name: str, region_name: str = None) -> dict:
    if is_local_runtime():
        return get_local_secret(secret_name)
    return get_aws_secret(secret_name, region_name=region_name)


def get_local_secret(secret_name: str) -> dict:
    secrets_path = get_local_secrets_path()
    if not secrets_path.exists():
        raise ValueError(f"local secrets file not found: {secrets_path}")

    secrets_payload = json.loads(secrets_path.read_text(encoding="utf-8"))
    if secret_name not in secrets_payload:
        raise ValueError(f"secret '{secret_name}' not found in local secrets file {secrets_path}")

    secret_value = secrets_payload[secret_name]
    if not isinstance(secret_value, dict):
        raise ValueError(f"secret '{secret_name}' must be a JSON object")

    return secret_value.copy()


def get_aws_secret(secret_name: str, region_name: str = None) -> dict:
    resolved_region = (
        region_name
        or os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or "us-west-2"
    )
    session = boto3.session.Session()
    client = session.client(
        service_name="secretsmanager",
        region_name=resolved_region,
    )

    try:
        get_secret_value_response = client.get_secret_value(SecretId=secret_name)
    except ClientError as exc:
        raise exc

    if "SecretString" in get_secret_value_response:
        secret = get_secret_value_response["SecretString"]
        return json.loads(secret)

    decoded_binary_secret = base64.b64decode(get_secret_value_response["SecretBinary"])
    return json.loads(decoded_binary_secret)
