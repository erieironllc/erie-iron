import datetime
import time

from erieiron_common import cloudformation_log_reader, common
from erieiron_common.enums import AwsEnv


def test_read_cloudformation_stack_activity_returns_full_sections(monkeypatch):
    def fake_collect_stack_activity(region, stack_name, deployment_start_datetime):
        return [
            "CloudFormation stack events since deployment start:",
            f"{stack_name} event summary",
        ]

    monkeypatch.setattr(
        cloudformation_log_reader,
        "collect_stack_activity_sections",
        fake_collect_stack_activity,
    )
    monkeypatch.setattr(
        cloudformation_log_reader,
        "extract_cloudwatch_lambda_logs",
        lambda **_: "lambda diagnostics",
    )

    rendered = cloudformation_log_reader.read_cloudformation_stack_activity(
        "us-east-1",
        "sample-stack",
        AwsEnv.DEV,
        time.time(),
        local_logs="local run logs",
    )

    assert "CloudFormation activity for stack 'sample-stack':" in rendered
    assert "sample-stack event summary" in rendered
    assert "lambda diagnostics" in rendered


def test_get_cloudformation_activity_includes_success_and_failure_events(monkeypatch):
    base_time = datetime.datetime.now(datetime.timezone.utc)
    deployment_start = base_time - datetime.timedelta(minutes=10)

    sample_events = [
        {
            "Timestamp": base_time - datetime.timedelta(minutes=5),
            "StackName": "sample-stack",
            "LogicalResourceId": "Bucket",
            "ResourceType": "AWS::S3::Bucket",
            "ResourceStatus": "CREATE_IN_PROGRESS",
            "ResourceStatusReason": "Provisioning",
            "PhysicalResourceId": "bucket-123",
            "ClientRequestToken": "token-1",
        },
        {
            "Timestamp": base_time - datetime.timedelta(minutes=4),
            "StackName": "sample-stack",
            "LogicalResourceId": "Topic",
            "ResourceType": "AWS::SNS::Topic",
            "ResourceStatus": "CREATE_FAILED",
            "ResourceStatusReason": "Invalid permissions",
            "PhysicalResourceId": "topic-123",
            "ClientRequestToken": "token-2",
        },
    ]

    monkeypatch.setattr(
        cloudformation_log_reader,
        "collect_recent_events",
        lambda cf_client, deployment_start_datetime, target_stack, visited: (sample_events, []),
    )

    class DummyCloudformationClient:
        def describe_stack_resource(self, StackName, LogicalResourceId):  # noqa: N802 (AWS casing)
            return {
                "StackResourceDetail": {
                    "StackName": StackName,
                    "LogicalResourceId": LogicalResourceId,
                }
            }

    monkeypatch.setattr(
        cloudformation_log_reader.boto3,
        "client",
        lambda service, region_name=None: DummyCloudformationClient(),
    )

    activity_lines = cloudformation_log_reader.get_cloudformation_activity(
        "us-east-1",
        "sample-stack",
        deployment_start,
    )
    rendered = common.safe_join(activity_lines, "\n")

    assert "CREATE_IN_PROGRESS" in rendered
    assert "CREATE_FAILED" in rendered
    assert "Detailed resource descriptions for top failures" in rendered
