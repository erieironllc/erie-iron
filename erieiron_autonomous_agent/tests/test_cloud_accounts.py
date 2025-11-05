import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from erieiron_autonomous_agent.models import Business, CloudAccount, Initiative, InfrastructureStack
from erieiron_autonomous_agent.utils import cloud_accounts
from erieiron_common.enums import BusinessIdeaSource, CloudProvider, EnvironmentType, InfrastructureStackType, Level


@pytest.fixture
def business(db):
    return Business.objects.create(
        name="CloudBiz",
        source=BusinessIdeaSource.HUMAN,
        service_token="cloud-biz",
        domain="example.com",
    )


@pytest.mark.django_db
def test_api_business_cloud_account_create_sets_defaults(client, business):
    url = reverse("api_business_cloud_account_create", args=[business.id])
    payload = {
        "name": "Primary AWS",
        "provider": "aws",
        "account_identifier": "123456789012",
        "region": "us-east-1",
        "is_default_dev": True,
        "is_default_production": False,
        "credentials": {
            "role_arn": "arn:aws:iam::123456789012:role/Example",
            "external_id": "secret-ext",
            "session_duration": 1800,
        },
    }

    with (
        patch(
            "erieiron_ui.views.cloud_accounts.store_credentials_secret",
            return_value="arn:secret:123",
        ) as mock_store_secret,
        patch("erieiron_ui.views.cloud_accounts.clear_cached_credentials") as mock_clear_cache,
    ):
        response = client.post(url, data=json.dumps(payload), content_type="application/json")

    assert response.status_code == 200
    data = response.json()
    account_data = data["account"]
    assert account_data["name"] == "Primary AWS"
    assert account_data["is_default_dev"] is True
    assert account_data["metadata"]["region"] == "us-east-1"

    account = CloudAccount.objects.get(id=account_data["id"])
    assert account.is_default_dev is True
    assert account.credentials_secret_arn == "arn:secret:123"
    mock_store_secret.assert_called_once()
    mock_clear_cache.assert_called_once_with(account.id)


@pytest.mark.django_db
def test_api_business_cloud_account_update_rotates_secret(client, business):
    initial = CloudAccount.objects.create(
        business=business,
        name="Default Dev",
        provider=CloudProvider.AWS.value,
        credentials_secret_arn="arn:secret:orig",
        is_default_dev=True,
    )
    other = CloudAccount.objects.create(
        business=business,
        name="Secondary",
        provider=CloudProvider.AWS.value,
    )

    url = reverse(
        "api_business_cloud_account_update",
        args=[business.id, other.id],
    )
    payload = {
        "is_default_dev": True,
        "credentials": {
            "role_arn": "arn:aws:iam::123456789012:role/Rotated",
            "session_duration": 2000,
        },
    }

    with (
        patch(
            "erieiron_ui.views.cloud_accounts.store_credentials_secret",
            return_value="arn:secret:new",
        ) as mock_store_secret,
        patch("erieiron_ui.views.cloud_accounts.clear_cached_credentials") as mock_clear_cache,
    ):
        response = client.post(url, data=json.dumps(payload), content_type="application/json")

    assert response.status_code == 200
    data = response.json()
    assert data["account"]["is_default_dev"] is True

    initial.refresh_from_db()
    other.refresh_from_db()
    assert initial.is_default_dev is False
    assert other.credentials_secret_arn == "arn:secret:new"
    mock_store_secret.assert_called_once()
    mock_clear_cache.assert_called_once_with(other.id)


@pytest.mark.django_db
def test_infrastructure_stack_get_assigns_default_cloud_account(business):
    default_account = CloudAccount.objects.create(
        business=business,
        name="Dev Account",
        provider=CloudProvider.AWS.value,
        is_default_dev=True,
    )
    initiative = Initiative.objects.create(
        id="init-1",
        business=business,
        title="Test Initiative",
        description="desc",
        priority=Level.MEDIUM,
    )

    stack = InfrastructureStack.get(
        initiative,
        InfrastructureStackType.APPLICATION,
        EnvironmentType.DEV,
    )

    assert stack.cloud_account_id == default_account.id


@pytest.mark.django_db
def test_build_aws_env_uses_cached_credentials(monkeypatch, business):
    # Ensure cache is clean for deterministic assertions
    cloud_accounts._CREDENTIAL_CACHE.clear()

    account = CloudAccount.objects.create(
        business=business,
        name="Prod",
        provider=CloudProvider.AWS.value,
        credentials_secret_arn="secret-name",
        metadata={"region": "us-east-2"},
    )

    secret_payload = {
        "role_arn": "arn:aws:iam::123456789012:role/Test",
        "session_duration": 1800,
    }
    assume_calls = []

    def fake_assume_role(**kwargs):
        assume_calls.append(kwargs)
        return {
            "Credentials": {
                "AccessKeyId": "AKIA...",
                "SecretAccessKey": "secret",
                "SessionToken": "token",
                "Expiration": timezone.now() + timedelta(hours=1),
            }
        }

    monkeypatch.setattr(
        cloud_accounts.aws_utils,
        "get_secret",
        lambda _: secret_payload,
    )
    monkeypatch.setattr(
        cloud_accounts.boto3,
        "client",
        lambda service: SimpleNamespace(assume_role=fake_assume_role),
    )

    env = cloud_accounts.build_aws_env(account, EnvironmentType.PRODUCTION)
    assert env["AWS_ACCESS_KEY_ID"] == "AKIA..."
    assert env["AWS_DEFAULT_REGION"] == "us-east-2"
    assert len(assume_calls) == 1

    # Second call should reuse cache and avoid another assume
    env2 = cloud_accounts.build_aws_env(account, EnvironmentType.PRODUCTION)
    assert env2["AWS_ACCESS_KEY_ID"] == "AKIA..."
    assert len(assume_calls) == 1

    cloud_accounts.clear_cached_credentials(account.id)
