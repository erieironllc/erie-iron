from contextlib import nullcontext
from types import SimpleNamespace
from unittest import mock

from botocore.exceptions import ClientError, OperationNotPageableError
from erieiron_autonomous_agent.coding_agents.self_driving_coder_agent import prepare_stack_for_update
from erieiron_autonomous_agent.models import SelfDrivingTask, Task
from erieiron_common import domain_manager
from erieiron_common.enums import AwsEnv


class DummySelfDrivingTask:
    def __init__(self, stack_identifier: str, domain: str | None = None):
        self._stack_identifier = stack_identifier
        self.domain = domain
        self.saved_fields = None

    def get_cloudformation_key_prefix(self, _aws_env: AwsEnv) -> str:
        return self._stack_identifier

    def save(self, update_fields=None):  # pragma: no cover - trivial setter
        self.saved_fields = update_fields


def _build_task(stack_identifier: str, business_domain: str | None = "example.com", *, needs_domain: bool = False):
    business = SimpleNamespace(
        domain=business_domain,
        needs_domain=needs_domain,
        service_token="svc-token",
    )
    initiative = SimpleNamespace(business=business)

    return SimpleNamespace(
        id="task_123",
        initiative=initiative,
        selfdrivingtask=DummySelfDrivingTask(stack_identifier),
    )


def test_get_domain_and_cert_production_prefers_business_domain():
    task = _build_task(stack_identifier="stackid", business_domain="Example.COM")

    with mock.patch("erieiron_autonomous_agent.models.aws_utils.client", return_value=object()), \
            mock.patch("erieiron_autonomous_agent.models.domain_manager.find_hosted_zone_id", return_value="HZ123") as mock_find_zone, \
            mock.patch("erieiron_autonomous_agent.models.domain_manager.find_certificate_arn", return_value="arn:example") as mock_find_cert, \
            mock.patch("erieiron_autonomous_agent.models.domain_manager.manage_domain") as mock_manage_domain:
        domain, hosted_zone_id, certificate_arn = Task.get_domain_and_cert(task, AwsEnv.PRODUCTION)

    assert domain == "example.com"
    assert hosted_zone_id == "HZ123"
    assert certificate_arn == "arn:example"
    assert task.selfdrivingtask.domain == "example.com"
    assert task.selfdrivingtask.saved_fields == ["domain"]
    mock_manage_domain.assert_not_called()
    mock_find_zone.assert_called_with(mock.ANY, "example.com")
    mock_find_cert.assert_called_with("example.com", AwsEnv.PRODUCTION.get_aws_region())


def test_get_domain_and_cert_dev_uses_stack_identifier():
    task = _build_task(stack_identifier="stacktoken", business_domain="example.com")

    with mock.patch("erieiron_autonomous_agent.models.aws_utils.client", return_value=object()), \
            mock.patch("erieiron_autonomous_agent.models.domain_manager.find_hosted_zone_id", return_value="HZ456"), \
            mock.patch("erieiron_autonomous_agent.models.domain_manager.find_certificate_arn", return_value="arn:dev"):
        domain, hosted_zone_id, certificate_arn = Task.get_domain_and_cert(task, AwsEnv.DEV)

    assert domain == "stacktoken.example.com"
    assert hosted_zone_id == "HZ456"
    assert certificate_arn == "arn:dev"
    assert task.selfdrivingtask.domain == "stacktoken.example.com"


def test_rotate_cloudformation_stack_name_refreshes_domain():
    task_mock = mock.Mock()
    task_mock.get_domain_and_cert.return_value = ("stacktoken.example.com", "HZ", "arn")

    business = SimpleNamespace(service_token="svc-token")
    self_driving_task = SelfDrivingTask()
    self_driving_task.id = "task-id"
    self_driving_task.business = business
    self_driving_task.task = task_mock
    self_driving_task.cloudformation_stack_name = "old-stack"

    dummy_manager = mock.Mock()
    dummy_manager.filter.return_value = dummy_manager

    with mock.patch("erieiron_autonomous_agent.models.boto3.client") as mock_boto_client, \
            mock.patch("erieiron_autonomous_agent.models.AgentTombstone.objects.update_or_create", return_value=(None, True)), \
            mock.patch.object(SelfDrivingTask, "_generate_unique_cloudformation_stack_name", return_value="new-stack"), \
            mock.patch.object(SelfDrivingTask, "objects", dummy_manager), \
            mock.patch("erieiron_autonomous_agent.models.transaction.atomic", return_value=nullcontext()) as mock_atomic, \
            mock.patch.object(self_driving_task, "refresh_from_db", autospec=True) as mock_refresh:
        mock_boto_client.return_value = mock.Mock(delete_stack=mock.Mock())
        result = self_driving_task.rotate_cloudformation_stack_name(AwsEnv.DEV)

    assert result == "new-stack"
    task_mock.get_domain_and_cert.assert_called_once_with(AwsEnv.DEV)
    mock_refresh.assert_called_with(fields=["cloudformation_stack_name", "cloudformation_stack_id"])
    dummy_manager.filter.assert_called_with(id="task-id")
    dummy_manager.update.assert_called_with(
        cloudformation_stack_name="new-stack",
        cloudformation_stack_id=None
    )
    mock_atomic.assert_called_once()


def test_prepare_stack_for_update_rotates_stack_and_resyncs_metadata():
    config = mock.Mock()
    config.aws_env = AwsEnv.DEV
    config.log = mock.Mock()
    config.refresh_domain_metadata = mock.Mock()
    config.self_driving_task.get_cloudformation_stack_name.return_value = "old-stack"
    config.self_driving_task.rotate_cloudformation_stack_name.return_value = "new-stack"

    docker_env = {}

    cf_client = mock.Mock()
    cf_client.exceptions = SimpleNamespace(ClientError=Exception)

    with mock.patch(
        "erieiron_autonomous_agent.coding_agents.self_driving_coder_agent.boto3.client",
        return_value=cf_client
    ), mock.patch(
        "erieiron_autonomous_agent.coding_agents.self_driving_coder_agent.get_stack",
        side_effect=[{"StackStatus": "DELETE_IN_PROGRESS"}]
    ), mock.patch(
        "erieiron_autonomous_agent.coding_agents.self_driving_coder_agent.sync_stack_identity"
    ) as mock_sync:
        result = prepare_stack_for_update(config, docker_env)

    assert result == "new-stack"
    config.self_driving_task.rotate_cloudformation_stack_name.assert_called_once_with(
        AwsEnv.DEV,
        status="DELETE_IN_PROGRESS",
        reason="Stack observed rolling back or deleting before deployment"
    )
    config.refresh_domain_metadata.assert_called_once_with()
    mock_sync.assert_called_once_with(config, docker_env)


def test_create_hosted_zone_reuses_existing_zone():
    chosen_domain = "articleparse.com"

    route53_stub = mock.Mock()
    route53_stub.list_hosted_zones_by_name.return_value = {
        "HostedZones": [
            {
                "Name": f"{chosen_domain}.",
                "Id": "/hostedzone/ZONE123"
            }
        ]
    }
    route53_stub.list_hosted_zones.return_value = {
        "HostedZones": []
    }
    route53_stub.create_hosted_zone.side_effect = AssertionError("should not create a duplicate hosted zone")

    with mock.patch("erieiron_common.domain_manager.aws_utils.client", return_value=route53_stub), \
            mock.patch("erieiron_common.domain_manager._wait_for_hosted_zone_visible") as mock_wait:
        hosted_zone_id = domain_manager.create_hosted_zone(chosen_domain)

    assert hosted_zone_id == "ZONE123"
    route53_stub.create_hosted_zone.assert_not_called()
    route53_stub.list_hosted_zones_by_name.assert_called_once()
    mock_wait.assert_not_called()


def test_create_hosted_zone_waits_for_visibility():
    chosen_domain = "articleparse.com"

    route53_stub = mock.Mock()
    route53_stub.list_hosted_zones_by_name.return_value = {"HostedZones": []}
    route53_stub.list_hosted_zones.return_value = {"HostedZones": [], "IsTruncated": False}
    route53_stub.create_hosted_zone.return_value = {
        "HostedZone": {"Id": "/hostedzone/ZONE999"}
    }

    with mock.patch("erieiron_common.domain_manager.aws_utils.client", return_value=route53_stub), \
            mock.patch("erieiron_common.domain_manager._wait_for_hosted_zone_visible") as mock_wait:
        domain_manager.create_hosted_zone(chosen_domain)

    mock_wait.assert_called_once_with(route53_stub, "ZONE999", "articleparse.com")


def test_create_hosted_zone_handles_already_exists():
    chosen_domain = "articleparse.com"

    route53_stub = mock.Mock()
    route53_stub.create_hosted_zone.side_effect = ClientError(
        {"Error": {"Code": "HostedZoneAlreadyExists"}},
        "CreateHostedZone"
    )

    with mock.patch("erieiron_common.domain_manager.aws_utils.client", return_value=route53_stub), \
            mock.patch("erieiron_common.domain_manager._resolve_existing_hosted_zone", return_value="ZONE321") as mock_resolve:
        hosted_zone_id = domain_manager.create_hosted_zone(chosen_domain)

    assert hosted_zone_id == "ZONE321"
    mock_resolve.assert_called_once_with(route53_stub, "articleparse.com")


def test_find_hosted_zone_scans_all_pages():
    chosen_domain = "suminbox.com"

    route53_stub = mock.Mock()
    route53_stub.list_hosted_zones_by_name.return_value = {
        "HostedZones": []
    }
    route53_stub.list_hosted_zones.side_effect = [
        {
            "HostedZones": [
                {
                    "Name": "something-else.com.",
                    "Id": "/hostedzone/OTHER",
                    "CallerReference": "ref-other"
                }
            ],
            "IsTruncated": True,
            "NextMarker": "token"
        },
        {
            "HostedZones": [
                {
                    "Name": f"{chosen_domain}.",
                    "Id": "/hostedzone/EXISTING",
                    "CallerReference": "erieiron-hz-parse"
                }
            ],
            "IsTruncated": False
        }
    ]

    hosted_zone_id = domain_manager.find_hosted_zone_id(route53_stub, chosen_domain)

    assert hosted_zone_id == "EXISTING"
    assert route53_stub.list_hosted_zones.call_count == 2


def test_find_hosted_zone_handles_non_pageable_clients():
    chosen_domain = "suminbox.com"

    route53_stub = mock.Mock()
    route53_stub.list_hosted_zones_by_name.side_effect = OperationNotPageableError(operation_name="list_hosted_zones_by_name")
    route53_stub.list_hosted_zones.return_value = {
        "HostedZones": [
            {
                "Name": f"{chosen_domain}.",
                "Id": "/hostedzone/EXISTING",
                "CallerReference": "erieiron-hz-parse"
            }
        ],
        "IsTruncated": False
    }
    hosted_zone_id = domain_manager.find_hosted_zone_id(route53_stub, chosen_domain)

    assert hosted_zone_id == "EXISTING"
    route53_stub.list_hosted_zones.assert_called_once()


def test_certificate_validation_handles_existing_records():
    validation_record = {
        "Name": "_token.suminbox.com.",
        "Type": "CNAME",
        "Value": "_validation.acm-validations.aws."
    }

    acm_stub = mock.Mock()
    acm_stub.describe_certificate.return_value = {
        "Certificate": {
            "DomainValidationOptions": [
                {
                    "ValidationMethod": "DNS",
                    "ResourceRecord": validation_record
                }
            ]
        }
    }

    route53_stub = mock.Mock()
    paginator_stub = mock.Mock()
    paginator_stub.paginate.return_value = [
        {
            "ResourceRecordSets": [
                {
                    "Name": validation_record["Name"],
                    "Type": validation_record["Type"],
                    "ResourceRecords": [{"Value": validation_record["Value"]}]
                }
            ]
        }
    ]
    route53_stub.get_paginator.return_value = paginator_stub
    route53_stub.change_resource_record_sets.side_effect = ClientError(
        {"Error": {"Code": "InvalidChangeBatch", "Message": "duplicate"}},
        "ChangeResourceRecordSets"
    )

    with mock.patch("erieiron_common.domain_manager.aws_utils.client", return_value=route53_stub):
        domain_manager._ensure_certificate_dns_validation(  # pylint: disable=protected-access
            acm_stub,
            certificate_arn="arn:aws:acm:::certificate/123",
            hosted_zone_id="ZONE123"
        )

    route53_stub.change_resource_record_sets.assert_called_once()


def test_domain_available_handles_unsupported_tld():
    route53domains_stub = mock.Mock()
    route53domains_stub.check_domain_availability.side_effect = ClientError(
        {"Error": {"Code": "UnsupportedTLD", "Message": "unsupported"}},
        "CheckDomainAvailability"
    )

    with mock.patch("erieiron_common.domain_manager.get_route53domains_client", return_value=route53domains_stub):
        assert domain_manager.domain_available("example.ai") is False


class DummyBusiness(SimpleNamespace):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._saves = []

    def save(self, update_fields=None):
        self._saves.append(tuple(update_fields) if update_fields else None)


def test_manage_domain_reuses_stored_hosted_zone():
    business = DummyBusiness(
        needs_domain=True,
        domain="ArticleParser.io",
        route53_hosted_zone_id="HZ123",
        service_token="svc",
    )

    route53_stub = mock.Mock()
    route53_stub.get_hosted_zone.return_value = {
        "HostedZone": {
            "Name": "articleparser.io.",
            "Id": "/hostedzone/HZ123"
        }
    }

    with mock.patch("erieiron_common.domain_manager.aws_utils.client", return_value=route53_stub), \
            mock.patch("erieiron_common.domain_manager.ensure_wildcard_certificate"), \
            mock.patch("erieiron_common.domain_manager.update_dns"), \
            mock.patch("erieiron_common.domain_manager.create_hosted_zone") as mock_create:
        domain_manager.manage_domain(business)

    mock_create.assert_not_called()
    assert business.domain == "articleparser.io"
    assert business.route53_hosted_zone_id == "HZ123"
    assert ("domain",) in business._saves or tuple(["domain"]) in business._saves


def test_manage_domain_creates_and_persists_hosted_zone():
    business = DummyBusiness(
        needs_domain=True,
        domain="NewSite.IO",
        route53_hosted_zone_id=None,
        service_token="svc",
    )

    route53_stub = mock.Mock()
    route53_stub.list_hosted_zones_by_name.return_value = {"HostedZones": []}
    route53_stub.list_hosted_zones.return_value = {"HostedZones": [], "IsTruncated": False}

    with mock.patch("erieiron_common.domain_manager.aws_utils.client", return_value=route53_stub), \
            mock.patch("erieiron_common.domain_manager.ensure_wildcard_certificate"), \
            mock.patch("erieiron_common.domain_manager.update_dns"), \
            mock.patch("erieiron_common.domain_manager.create_hosted_zone", return_value="NEWZONE") as mock_create:
        domain_manager.manage_domain(business)

    mock_create.assert_called_once_with("newsite.io")
    assert business.domain == "newsite.io"
    assert business.route53_hosted_zone_id == "NEWZONE"
    assert ("domain",) in business._saves or tuple(["domain"]) in business._saves
    assert ("route53_hosted_zone_id",) in business._saves or tuple(["route53_hosted_zone_id"]) in business._saves
