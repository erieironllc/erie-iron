import datetime
from unittest import mock

from erieiron_common import cloudformation_log_reader


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
            resource_descriptions: dict[tuple[str, str], dict],
            event_page_sizes: dict[str, int] | None = None,
    ):
        self._events_by_stack = events_by_stack
        self._resource_pages = resource_pages
        self._resource_descriptions = resource_descriptions
        self._event_page_sizes = event_page_sizes or {}

    def describe_stack_events(self, StackName: str, NextToken: str | None = None):  # noqa: N802
        events = list(self._events_by_stack.get(StackName, []))
        page_size = self._event_page_sizes.get(StackName)
        if not page_size or page_size <= 0:
            return {"StackEvents": events}

        page_index = 0
        if NextToken:
            try:
                page_index = int(NextToken.rsplit(":", 1)[-1])
            except (ValueError, AttributeError):  # pragma: no cover - defensive fallback for malformed tokens
                page_index = 0

        start = page_index * page_size
        end = start + page_size
        page_events = events[start:end]
        response = {"StackEvents": page_events}

        if end < len(events):
            response["NextToken"] = f"{StackName}:{page_index + 1}"

        return response

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

    with mock.patch("erieiron_common.cloudformation_log_reader.boto3.client", side_effect=fake_boto3_client), \
            mock.patch("erieiron_common.cloudformation_log_reader.extract_cloudwatch_stack_logs_for_window", return_value=""), \
            mock.patch("erieiron_common.cloudformation_log_reader.common.get_now", return_value=frozen_now):
        report = cloudformation_log_reader.read_cloudformation_failures(
            aws_region="us-west-2",
            stack_name=parent_stack_identifier,
            start_time=start_time,
            local_logs=None,
   )

    assert "Duplicate Resource Record" in report
    # Ensure the nested stack information is surfaced alongside the failure events.
    assert "CloudFormation failure events:" in report


def test_read_cloudformation_failures_handles_paginated_stack_events():
    start_time = 1_800_000_000
    start_dt = datetime.datetime.fromtimestamp(start_time, tz=datetime.timezone.utc)

    parent_stack_identifier = "parent-stack"
    nested_stack_identifier = "arn:aws:cloudformation:region:account:stack/nested-stack/pagination"

    parent_events = [
        {
            "Timestamp": start_dt + datetime.timedelta(seconds=120),
            "StackName": parent_stack_identifier,
            "StackId": "arn:aws:cloudformation:region:account:stack/parent-stack/root",
            "LogicalResourceId": "NestedStack",
            "ResourceType": "AWS::CloudFormation::Stack",
            "ResourceStatus": "CREATE_COMPLETE",
            "ResourceStatusReason": "Resource creation complete",
            "PhysicalResourceId": nested_stack_identifier,
            "ClientRequestToken": "token-parent",
        }
    ]

    failure_logical_id = "SesSpfRecord"
    failure_reason = "No hosted zone found with ID: None"

    nested_events: list[dict] = []
    # Produce events in reverse chronological order, matching AWS API behaviour, where
    # the failure event is older than an entire first page of results.
    for offset in range(25):
        idx = 24 - offset
        ts = start_dt + datetime.timedelta(seconds=idx)
        nested_events.append(
            {
                "Timestamp": ts,
                "StackName": "nested-stack",
                "StackId": nested_stack_identifier,
                "LogicalResourceId": failure_logical_id if idx == 5 else f"OtherResource{idx}",
                "ResourceType": "AWS::Route53::RecordSet",
                "ResourceStatus": "CREATE_FAILED" if idx == 5 else "CREATE_COMPLETE",
                "ResourceStatusReason": failure_reason if idx == 5 else "Successfully created",
                "PhysicalResourceId": f"resource-{idx}",
                "ClientRequestToken": f"token-{idx}",
            }
        )

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
            (nested_stack_identifier, failure_logical_id): {
                "StackResourceDetail": {
                    "StackName": "nested-stack",
                    "LogicalResourceId": failure_logical_id,
                }
            }
        },
        event_page_sizes={
            parent_stack_identifier: 10,
            nested_stack_identifier: 10,
        }
    )

    def fake_boto3_client(service_name, region_name=None):
        if service_name == "cloudformation":
            return cf_client
        if service_name == "cloudtrail":
            return DummyCloudTrailClient()
        raise AssertionError(f"Unexpected boto3 client request: {service_name}")

    frozen_now = start_dt + datetime.timedelta(minutes=10)

    with mock.patch("erieiron_common.cloudformation_log_reader.boto3.client", side_effect=fake_boto3_client), \
            mock.patch("erieiron_common.cloudformation_log_reader.extract_cloudwatch_stack_logs_for_window", return_value=""), \
            mock.patch("erieiron_common.cloudformation_log_reader.common.get_now", return_value=frozen_now):
        report = cloudformation_log_reader.read_cloudformation_failures(
            aws_region="us-west-2",
            stack_name=parent_stack_identifier,
            start_time=start_time,
            local_logs=None,
        )

    assert failure_reason in report
