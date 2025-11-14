from __future__ import annotations

import copy
from uuid import UUID

from django.db import migrations


BUSINESS_ID = UUID("97c2b33a-4ff5-4bd7-a2f2-e829db0fe3cc")

SHARED_VPC_CONFIG = {
    "vpc_id": "vpc-0069eeaf60f597540",
    "cidr_block": "10.90.0.0/16",
    "public_subnets": [
        {
            "name": "erie-iron-shared-public-a",
            "subnet_id": None,
            "cidr_block": "10.90.0.0/20",
            "availability_zone": None,
        },
        {
            "name": "erie-iron-shared-public-b",
            "subnet_id": None,
            "cidr_block": "10.90.16.0/20",
            "availability_zone": None,
        },
    ],
    "private_subnets": [
        {
            "name": "erie-iron-shared-private-a",
            "subnet_id": None,
            "cidr_block": "10.90.32.0/20",
            "availability_zone": None,
        },
        {
            "name": "erie-iron-shared-private-b",
            "subnet_id": None,
            "cidr_block": "10.90.48.0/20",
            "availability_zone": None,
        },
    ],
    "security_groups": {"rds_security_group_id": "sg-05578f857876a5108"},
}


def _should_update(existing_vpc_config: dict | None) -> bool:
    if not existing_vpc_config:
        return True

    required_fields = ["vpc_id", "cidr_block", "public_subnets", "private_subnets"]
    for field in required_fields:
        if not existing_vpc_config.get(field):
            return True
    security_groups = existing_vpc_config.get("security_groups") or {}
    if not security_groups.get("rds_security_group_id"):
        return True
    return False


def populate_vpc_metadata(apps, schema_editor):
    Business = apps.get_model("erieiron_autonomous_agent", "Business")
    CloudAccount = apps.get_model("erieiron_autonomous_agent", "CloudAccount")

    try:
        business = Business.objects.get(id=BUSINESS_ID)
    except Business.DoesNotExist:
        return

    cloud_accounts = CloudAccount.objects.filter(business=business)
    for account in cloud_accounts:
        metadata = account.metadata or {}
        existing_vpc_config = metadata.get("vpc")
        if not _should_update(existing_vpc_config):
            continue

        metadata = {**metadata, "vpc": copy.deepcopy(SHARED_VPC_CONFIG)}
        account.metadata = metadata
        account.save(update_fields=["metadata"])


def remove_vpc_metadata(apps, schema_editor):
    Business = apps.get_model("erieiron_autonomous_agent", "Business")
    CloudAccount = apps.get_model("erieiron_autonomous_agent", "CloudAccount")

    try:
        business = Business.objects.get(id=BUSINESS_ID)
    except Business.DoesNotExist:
        return

    cloud_accounts = CloudAccount.objects.filter(business=business)
    for account in cloud_accounts:
        metadata = account.metadata or {}
        existing_vpc = metadata.get("vpc")
        if not existing_vpc:
            continue
        if existing_vpc.get("vpc_id") != SHARED_VPC_CONFIG["vpc_id"]:
            continue
        metadata.pop("vpc", None)
        account.metadata = metadata
        account.save(update_fields=["metadata"])


class Migration(migrations.Migration):
    dependencies = [
        ("erieiron_autonomous_agent", "0093_cloudaccount_infrastructurestack_cloud_account_and_more"),
    ]

    operations = [
        migrations.RunPython(populate_vpc_metadata, remove_vpc_metadata),
    ]
