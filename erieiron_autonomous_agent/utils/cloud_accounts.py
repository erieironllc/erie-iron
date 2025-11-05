import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError
from django.utils import timezone

from erieiron_autonomous_agent.models import CloudAccount
from erieiron_common import aws_utils, common
from erieiron_common.enums import CloudProvider, EnvironmentType

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CachedAwsCredentials:
    access_key_id: str
    secret_access_key: str
    session_token: Optional[str]
    expiration: datetime


# Cache assumed credentials keyed by cloud account id
_CREDENTIAL_CACHE: dict[str, CachedAwsCredentials] = {}
_CACHE_SAFETY_WINDOW = timedelta(minutes=5)


def _now() -> timezone.datetime:
    return timezone.now()


def store_credentials_secret(cloud_account: CloudAccount, payload: Dict[str, Any]) -> str:
    """Persist provider credential payload for a cloud account.

    Returns the secret identifier that was written so callers can store it on the model.
    """
    if not isinstance(payload, dict):
        raise ValueError("Cloud account credential payload must be a dict")
    secret_name = cloud_account.build_secret_name()
    logger.info(
        "Storing credential payload for cloud account",
        extra={
            "cloud_account_id": str(cloud_account.id),
            "business_id": str(cloud_account.business_id),
            "provider": cloud_account.provider,
        },
    )
    arn_or_name = aws_utils.put_secret(secret_name, payload)
    return arn_or_name or secret_name


def load_credentials_secret(cloud_account: CloudAccount) -> Dict[str, Any]:
    secret_id = cloud_account.credentials_secret_arn or cloud_account.build_secret_name()
    try:
        return aws_utils.get_secret(secret_id)
    except ClientError as exc:
        logger.exception(
            "Failed to load credential secret for cloud account",
            extra={
                "cloud_account_id": str(cloud_account.id),
                "business_id": str(cloud_account.business_id),
                "provider": cloud_account.provider,
            },
        )
        raise exc


def _base_session_credentials() -> CachedAwsCredentials:
    session = boto3.session.Session()
    frozen = session.get_credentials().get_frozen_credentials() if session.get_credentials() else None
    if not frozen:
        raise RuntimeError("Unable to locate base AWS credentials for CloudAccount operations")
    # Static credentials may not expose expiration; treat as long-lived.
    expiration = _now() + timedelta(hours=8)
    return CachedAwsCredentials(
        access_key_id=frozen.access_key,
        secret_access_key=frozen.secret_key,
        session_token=frozen.token,
        expiration=expiration,
    )


def get_aws_credentials(cloud_account: Optional[CloudAccount], *, force_refresh: bool = False) -> CachedAwsCredentials:
    """Return AWS credentials for the provided cloud account, refreshing when necessary."""
    if cloud_account is None:
        return _base_session_credentials()

    if CloudProvider.AWS.neq(cloud_account.provider):
        raise NotImplementedError(f"Provider {cloud_account.provider} is not supported yet")

    cache_key = str(cloud_account.id)
    cached = _CREDENTIAL_CACHE.get(cache_key)
    if not force_refresh and cached and cached.expiration - _now() > _CACHE_SAFETY_WINDOW:
        return cached

    secret_payload = load_credentials_secret(cloud_account)
    role_arn = common.get(secret_payload, "role_arn")
    if not role_arn:
        raise RuntimeError("CloudAccount AWS credential payload missing role_arn")

    session_name = common.get(secret_payload, "session_name") or f"erieiron-{cloud_account.id}"
    external_id = common.get(secret_payload, "external_id")
    duration_seconds = int(common.get(secret_payload, "session_duration") or 3600)

    sts_client = boto3.client("sts")
    assume_kwargs = {
        "RoleArn": role_arn,
        "RoleSessionName": session_name,
        "DurationSeconds": duration_seconds,
    }
    if external_id:
        assume_kwargs["ExternalId"] = external_id

    logger.info(
        "Assuming role for cloud account",
        extra={
            "cloud_account_id": str(cloud_account.id),
            "business_id": str(cloud_account.business_id),
            "provider": cloud_account.provider,
            "role_arn": role_arn,
        },
    )
    response = sts_client.assume_role(**assume_kwargs)
    credentials = response.get("Credentials") or {}
    expiration = credentials.get("Expiration")
    if not expiration:
        expiration = _now() + timedelta(hours=1)
    elif not timezone.is_aware(expiration):
        expiration = timezone.make_aware(expiration)

    cached = CachedAwsCredentials(
        access_key_id=credentials.get("AccessKeyId"),
        secret_access_key=credentials.get("SecretAccessKey"),
        session_token=credentials.get("SessionToken"),
        expiration=expiration,
    )
    _CREDENTIAL_CACHE[cache_key] = cached
    return cached


def clear_cached_credentials(cloud_account_id: str) -> None:
    _CREDENTIAL_CACHE.pop(str(cloud_account_id), None)


def build_aws_env(cloud_account: Optional[CloudAccount], env_type: Optional[EnvironmentType]) -> Dict[str, str]:
    credentials = get_aws_credentials(cloud_account)
    region_candidates = []
    if env_type:
        try:
            region_candidates.append(env_type.get_aws_region())
        except Exception:  # Defensive: custom env types might not expose region
            pass
    if cloud_account and isinstance(cloud_account.metadata, dict):
        metadata_region = cloud_account.metadata.get("region") or cloud_account.metadata.get("default_region")
        if metadata_region:
            region_candidates.insert(0, metadata_region)
    region = common.first([candidate for candidate in region_candidates if candidate]) or "us-west-2"

    env = {
        "AWS_ACCESS_KEY_ID": credentials.access_key_id,
        "AWS_SECRET_ACCESS_KEY": credentials.secret_access_key,
        "AWS_DEFAULT_REGION": region,
        "AWS_REGION": region,
    }
    if credentials.session_token:
        env["AWS_SESSION_TOKEN"] = credentials.session_token
    if cloud_account:
        env["ERIEIRON_CLOUD_ACCOUNT_ID"] = str(cloud_account.id)
    return env
