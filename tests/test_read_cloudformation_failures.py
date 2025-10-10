import datetime
from unittest import mock

from erieiron_common import aws_utils


class DummyPaginator:
    def __init__(self, pages: dict[str, list[dict]]):
        self._pages = pages

    def paginate(self, StackName: str):  # noqa: N802 (AWS uses camelCase parameter names)
        return self._pages.get(StackName, [{"StackResourceSummaries": []}])


class DummyCloudFormationClient:
    def __init__(
            self,
            events_by_stack: dict[str, list[dict]],
            resource_pages: dict[str, list[dict]],
            resource_descriptions: dict[tuple[str, str], dict]
    ):
        self._events_by_stack = events_by_stack
        self._resource_pages = resource_pages
        self._resource_descriptions = resource_descriptions

    def describe_stack_events(self, StackName: str):  # noqa: N802
        return {"StackEvents": self._events_by_stack.get(StackName, [])}

    def get_paginator(self, operation_name: str):
        if operation_name != "list_stack_resources":  # pragma: no cover - defensive branch
            raise AssertionError(f"Unexpected paginator request: {operation_name}")
        return DummyPaginator(self._resource_pages)

    def describe_stack_resource(self, StackName: str, LogicalResourceId: str):  # noqa: N802
        return self._resource_descriptions[(StackName, LogicalResourceId)]


class DummyCloudTrailClient:
    @staticmethod
    def lookup_events(**_kwargs):
        return {"Events": []}


def test_read_cloudformation_failures_includes_nested_stack_failure_reason():
    start_time = 1_700_000_000
    start_dt = datetime.datetime.fromtimestamp(start_time, tz=datetime.timezone.utc)

    parent_stack_identifier = "parent-stack"
    nested_stack_identifier = "arn:aws:cloudformation:us-west-2:123456789012:stack/nested-stack/uuid"

    parent_events = [
        {
            "Timestamp": start_dt + datetime.timedelta(seconds=5),
            "StackName": parent_stack_identifier,
            "StackId": "arn:aws:cloudformation:us-west-2:123456789012:stack/parent-stack/uuid",
            "LogicalResourceId": "NestedStack",
            "ResourceType": "AWS::CloudFormation::Stack",
            "ResourceStatus": "UPDATE_FAILED",
            "ResourceStatusReason": "Resource creation cancelled",
            "PhysicalResourceId": nested_stack_identifier,
            "ClientRequestToken": "token-parent",
        }
    ]

    nested_events = [
        {
            "Timestamp": start_dt + datetime.timedelta(seconds=10),
            "StackName": "nested-stack",
            "StackId": nested_stack_identifier,
            "LogicalResourceId": "SesMxRecord",
            "ResourceType": "AWS::Route53::RecordSet",
            "ResourceStatus": "UPDATE_FAILED",
            "ResourceStatusReason": "Duplicate Resource Record: '10 inbound-smtp.us-west-2.amazonaws.com.'",
            "PhysicalResourceId": "record-resource",
            "ClientRequestToken": "token-nested",
        }
    ]

    cf_client = DummyCloudFormationClient(
        events_by_stack={
            parent_stack_identifier: parent_events,
            nested_stack_identifier: nested_events,
        },
        resource_pages={
            parent_stack_identifier: [
                {
                    "StackResourceSummaries": [
                        {
                            "LogicalResourceId": "NestedStack",
                            "PhysicalResourceId": nested_stack_identifier,
                            "ResourceType": "AWS::CloudFormation::Stack",
                        }
                    ]
                }
            ],
            nested_stack_identifier: [{"StackResourceSummaries": []}],
        },
        resource_descriptions={
            (nested_stack_identifier, "SesMxRecord"): {
                "StackResourceDetail": {
                    "StackName": "nested-stack",
                    "LogicalResourceId": "SesMxRecord",
                }
            }
        }
    )

    def fake_boto3_client(service_name, region_name=None):
        if service_name == "cloudformation":
            return cf_client
        if service_name == "cloudtrail":
            return DummyCloudTrailClient()
        raise AssertionError(f"Unexpected boto3 client request: {service_name}")

    frozen_now = start_dt + datetime.timedelta(minutes=10)

    with mock.patch("erieiron_common.aws_utils.boto3.client", side_effect=fake_boto3_client), \
            mock.patch("erieiron_common.aws_utils.extract_cloudwatch_stack_logs_for_window", return_value=""), \
            mock.patch("erieiron_common.aws_utils.common.get_now", return_value=frozen_now):
        report = aws_utils.read_cloudformation_failures(
            aws_region="us-west-2",
            stack_name=parent_stack_identifier,
            start_time=start_time,
            local_logs=None,
        )

    assert "Duplicate Resource Record" in report
    # Ensure the nested stack information is surfaced alongside the failure events.
    assert "CloudFormation failure events:" in report
