import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError
from django.utils import timezone

from erieiron_autonomous_agent.models import CloudAccount, InfrastructureStack, Business
from erieiron_common import aws_utils, common
from erieiron_common.enums import CloudProvider, EnvironmentType, InfrastructureStackType

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CachedAwsCredentials:
    role_arn: str
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
    
    # Debug: Log current AWS context before storing
    try:
        current_identity = boto3.client('sts').get_caller_identity()
        logger.info(
            "Storing credential payload for cloud account",
            extra={
                "cloud_account_id": str(cloud_account.id),
                "business_id": str(cloud_account.business_id),
                "provider": cloud_account.provider,
                "secret_name": secret_name,
                "aws_account": current_identity.get("Account"),
                "aws_user_arn": current_identity.get("Arn"),
            },
        )
    except Exception as e:
        logger.warning(f"Could not get AWS identity for secret storage: {e}")
        logger.info(
            "Storing credential payload for cloud account",
            extra={
                "cloud_account_id": str(cloud_account.id),
                "business_id": str(cloud_account.business_id),
                "provider": cloud_account.provider,
                "secret_name": secret_name,
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
        role_arn=frozen.role_arn,
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
    
    d = {
        "cloud_account_id": str(cloud_account.id),
        "business_id": str(cloud_account.business_id),
        "provider": cloud_account.provider,
        "role_arn": role_arn,
    }
    logger.info(f"Assuming role for cloud account {d}", extra=d)
    response = sts_client.assume_role(**assume_kwargs)
    credentials = response.get("Credentials") or {}
    expiration = credentials.get("Expiration")
    if not expiration:
        expiration = _now() + timedelta(hours=1)
    elif not timezone.is_aware(expiration):
        expiration = timezone.make_aware(expiration)
    
    cached = CachedAwsCredentials(
        role_arn=role_arn,
        access_key_id=credentials.get("AccessKeyId"),
        secret_access_key=credentials.get("SecretAccessKey"),
        session_token=credentials.get("SessionToken"),
        expiration=expiration,
    )
    _CREDENTIAL_CACHE[cache_key] = cached
    return cached


def clear_cached_credentials(cloud_account_id: str) -> None:
    _CREDENTIAL_CACHE.pop(str(cloud_account_id), None)


def build_cloud_credentials(stacks: list[InfrastructureStack]) -> Dict[str, str]:
    stacks = common.ensure_list(stacks)
    business = stacks[0].business
    env_type = EnvironmentType(stacks[0].env_type)
    
    stack_map = {InfrastructureStackType(s.stack_type): s for s in stacks}
    
    cloud_account = (
            common.get(stack_map, [InfrastructureStackType.APPLICATION, "cloud_account"])
            or common.get(stack_map, [InfrastructureStackType.FOUNDATION, "cloud_account"])
            or business.get_default_cloud_account(env_type)
            or Business.get_erie_iron_business().get_default_cloud_account(env_type)
    )
    
    cloud_account_credentials = get_aws_credentials(cloud_account)
    
    aws_region = env_type.get_aws_region()
    env = {
        "BUSINESS_CLOUD_ACCOUNT_ID": cloud_account.account_identifier,
        "AWS_ROLE_ARN": cloud_account_credentials.role_arn,
        "AWS_ACCESS_KEY_ID": cloud_account_credentials.access_key_id,
        "AWS_SECRET_ACCESS_KEY": cloud_account_credentials.secret_access_key,
        "AWS_DEFAULT_REGION": aws_region,
        "AWS_REGION": aws_region
    }
    
    if cloud_account_credentials.session_token:
        env["AWS_SESSION_TOKEN"] = cloud_account_credentials.session_token
    
    return env
