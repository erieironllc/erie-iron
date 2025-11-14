import types

import pytest

from erieiron_autonomous_agent.models import Business, CloudAccount
from erieiron_common import aws_utils
from erieiron_common.enums import BusinessIdeaSource


@pytest.fixture(autouse=True)
def stub_s3_cache(monkeypatch):
    class DummyCache:
        def __init__(self, *_args, **_kwargs):
            pass

    def fake_client(service_name, cloud_account=None, endpoint_url=None):
        client = types.SimpleNamespace()
        client.meta = types.SimpleNamespace(region_name="us-west-2")
        return client

    monkeypatch.setattr("erieiron_common.aws_utils.S3LocalCache", DummyCache)
    monkeypatch.setattr("erieiron_common.aws_utils.client", fake_client)


@pytest.fixture
def cloud_account(business):
    return CloudAccount.objects.create(
        business=business,
        name="test-dev",
        metadata={"vpc": {}},
    )


@pytest.fixture
def business():
    return Business.objects.create(name="Test Biz", source=BusinessIdeaSource.HUMAN)


@pytest.mark.django_db
def test_get_shared_vpc_uses_metadata_without_ec2(monkeypatch, cloud_account):
    cloud_account.metadata = {
        "vpc": {
            "vpc_id": "vpc-123",
            "cidr_block": "10.0.0.0/16",
            "public_subnets": [
                {
                    "name": "public-a",
                    "subnet_id": "subnet-public-a",
                    "cidr_block": "10.0.0.0/20",
                    "availability_zone": "us-west-2a",
                },
                {
                    "name": "public-b",
                    "subnet_id": "subnet-public-b",
                    "cidr_block": "10.0.16.0/20",
                    "availability_zone": "us-west-2b",
                },
            ],
            "private_subnets": [
                {
                    "name": "private-a",
                    "subnet_id": "subnet-private-a",
                    "cidr_block": "10.0.32.0/20",
                    "availability_zone": "us-west-2a",
                },
                {
                    "name": "private-b",
                    "subnet_id": "subnet-private-b",
                    "cidr_block": "10.0.48.0/20",
                    "availability_zone": "us-west-2b",
                },
            ],
            "security_groups": {"rds_security_group_id": "sg-123"},
        }
    }
    cloud_account.save(update_fields=["metadata"])

    interface = aws_utils.AwsInterface(cloud_account)

    def fail_on_ec2(service_name, endpoint_url=None):
        if service_name == "ec2":
            raise AssertionError("EC2 lookups should not be required when subnet IDs are present")
        return types.SimpleNamespace(meta=types.SimpleNamespace(region_name="us-west-2"))

    interface.client = types.MethodType(lambda self, service_name, endpoint_url=None: fail_on_ec2(service_name, endpoint_url), interface)

    shared_vpc = interface.get_shared_vpc()

    assert shared_vpc.vpc_id == "vpc-123"
    assert shared_vpc.public_subnet_ids == ["subnet-public-a", "subnet-public-b"]
    assert shared_vpc.private_subnet_ids == ["subnet-private-a", "subnet-private-b"]
    assert shared_vpc.rds_security_group_id == "sg-123"


@pytest.mark.django_db
def test_get_shared_vpc_hydrates_subnet_ids(monkeypatch, cloud_account):
    cloud_account.metadata = {
        "vpc": {
            "vpc_id": "vpc-123",
            "cidr_block": "10.0.0.0/16",
            "public_subnets": [
                {"name": "public-a", "cidr_block": "10.0.0.0/20", "availability_zone": None},
                {"name": "public-b", "cidr_block": "10.0.16.0/20", "availability_zone": None},
            ],
            "private_subnets": [
                {"name": "private-a", "cidr_block": "10.0.32.0/20", "availability_zone": None},
                {"name": "private-b", "cidr_block": "10.0.48.0/20", "availability_zone": None},
            ],
            "security_groups": {"rds_security_group_id": "sg-123"},
        }
    }
    cloud_account.save(update_fields=["metadata"])

    class DummyEc2Client:
        def __init__(self, payload):
            self._payload = payload

        def describe_subnets(self, **_kwargs):
            return self._payload

    ec2_response = {
        "Subnets": [
            {
                "SubnetId": "subnet-public-a",
                "CidrBlock": "10.0.0.0/20",
                "AvailabilityZone": "us-west-2a",
                "Tags": [{"Key": "Name", "Value": "public-a"}],
            },
            {
                "SubnetId": "subnet-public-b",
                "CidrBlock": "10.0.16.0/20",
                "AvailabilityZone": "us-west-2b",
                "Tags": [{"Key": "Name", "Value": "public-b"}],
            },
            {
                "SubnetId": "subnet-private-a",
                "CidrBlock": "10.0.32.0/20",
                "AvailabilityZone": "us-west-2a",
                "Tags": [{"Key": "Name", "Value": "private-a"}],
            },
            {
                "SubnetId": "subnet-private-b",
                "CidrBlock": "10.0.48.0/20",
                "AvailabilityZone": "us-west-2b",
                "Tags": [{"Key": "Name", "Value": "private-b"}],
            },
        ]
    }

    interface = aws_utils.AwsInterface(cloud_account)

    ec2_client = DummyEc2Client(ec2_response)

    def fake_client(service_name, endpoint_url=None):
        if service_name == "ec2":
            return ec2_client
        return types.SimpleNamespace(meta=types.SimpleNamespace(region_name="us-west-2"))

    interface.client = types.MethodType(lambda self, service_name, endpoint_url=None: fake_client(service_name, endpoint_url), interface)

    shared_vpc = interface.get_shared_vpc()

    cloud_account.refresh_from_db()
    cached_public = cloud_account.metadata["vpc"]["public_subnets"]

    assert shared_vpc.public_subnet_ids == ["subnet-public-a", "subnet-public-b"]
    assert shared_vpc.private_subnet_ids == ["subnet-private-a", "subnet-private-b"]
    assert cached_public[0]["subnet_id"] == "subnet-public-a"
    assert cached_public[0]["availability_zone"] == "us-west-2a"


@pytest.mark.django_db
def test_get_shared_vpc_requires_security_group(cloud_account):
    cloud_account.metadata = {
        "vpc": {
            "vpc_id": "vpc-123",
            "cidr_block": "10.0.0.0/16",
            "public_subnets": [
                {
                    "name": "public-a",
                    "subnet_id": "subnet-public-a",
                    "cidr_block": "10.0.0.0/20",
                    "availability_zone": "us-west-2a",
                },
                {
                    "name": "public-b",
                    "subnet_id": "subnet-public-b",
                    "cidr_block": "10.0.16.0/20",
                    "availability_zone": "us-west-2b",
                },
            ],
            "private_subnets": [
                {
                    "name": "private-a",
                    "subnet_id": "subnet-private-a",
                    "cidr_block": "10.0.32.0/20",
                    "availability_zone": "us-west-2a",
                },
                {
                    "name": "private-b",
                    "subnet_id": "subnet-private-b",
                    "cidr_block": "10.0.48.0/20",
                    "availability_zone": "us-west-2b",
                },
            ],
        }
    }
    cloud_account.save(update_fields=["metadata"])

    interface = aws_utils.AwsInterface(cloud_account)

    with pytest.raises(RuntimeError) as exc:
        interface.get_shared_vpc()

    assert "security_groups" in str(exc.value)
