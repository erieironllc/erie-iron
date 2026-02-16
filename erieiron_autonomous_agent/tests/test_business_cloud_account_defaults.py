import pytest

from erieiron_autonomous_agent.models import Business, CloudAccount
from erieiron_common.enums import BusinessIdeaSource, CloudProvider, EnvironmentType


def _bootstrap_template_account() -> CloudAccount:
    template_business = Business.get_erie_iron_business()
    template_business.cloud_accounts.all().delete()
    return CloudAccount.objects.create(
        business=template_business,
        name="erie-iron-production",
        provider=CloudProvider.AWS.value,
        account_identifier="782005355493",
        metadata={"region": "us-west-2", "vpc": {"vpc_id": "vpc-123"}},
        credentials_secret_arn="arn:aws:secretsmanager:us-west-2:782005355493:secret:control-plane",
        is_default_production=True,
    )


@pytest.mark.django_db
def test_get_default_cloud_account_autocreates_from_template():
    template_account = _bootstrap_template_account()
    target_business = Business.objects.create(
        name="Test Production Biz",
        source=BusinessIdeaSource.HUMAN,
    )

    created_account = target_business.get_default_cloud_account(EnvironmentType.PRODUCTION)

    assert created_account is not None
    assert created_account.business_id == target_business.id
    assert created_account.is_default_production
    assert created_account.account_identifier == template_account.account_identifier
    assert created_account.credentials_secret_arn == template_account.credentials_secret_arn
    assert created_account.metadata == template_account.metadata


@pytest.mark.django_db
def test_get_default_cloud_account_without_env_creates_for_missing_business():
    _bootstrap_template_account()
    target_business = Business.objects.create(
        name="Business Without Cloud Account",
        source=BusinessIdeaSource.HUMAN,
    )

    created_account = target_business.get_default_cloud_account()

    assert created_account is not None
    assert created_account.business_id == target_business.id
    assert created_account.is_default_production
    assert created_account.provider == CloudProvider.AWS.value
    assert created_account.metadata["region"] == "us-west-2"
