import datetime
import time

from erieiron_common import cloudformation_log_reader, common
from erieiron_common.enums import AwsEnv


def test_read_cloudformation_stack_activity_returns_full_sections(monkeypatch):
    def fake_collect_stack_activity(region, stack_name, deployment_start_datetime):
        return {
            "cloudformation": {
                "context": "CloudFormation stack events since deployment start",
                "events": [
                    {"summary": f"{stack_name} event summary"}
                ],
                "failure_events": [],
                "failed_logical_ids": [],
                "failed_resource_descriptions": [],
                "status_counts": {},
                "collection_errors": [],
                "errors": []
            },
            "cloudtrail": {"context": "", "events": [], "errors": []},
            "cloudwatch": {
                "context": "",
                "time_window": {},
                "stack_logs": None,
                "ecs_task_logs": None,
                "ecs_task_stop_reasons": None,
                "cloudwatch_alarms": None,
                "alb_error_logs": None,
                "errors": []
            },
        }

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
        AwsEnv.DEV,
        "sample-stack",
        time.time()
    )

    assert "sample-stack" in rendered["stacks"]
    stack_activity = rendered["stacks"]["sample-stack"]
    assert stack_activity["cloudformation"]["events"][0]["summary"] == "sample-stack event summary"
    assert rendered["lambda_logs"]["logs"] == "lambda diagnostics"


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
    assert len(activity_lines["events"]) == 2
    assert activity_lines["events"][0]["status"] == "CREATE_IN_PROGRESS"
    assert activity_lines["failure_events"][0]["status"] == "CREATE_FAILED"
    assert activity_lines["failed_resource_descriptions"][0]["logical_resource_id"] == "Topic"
    assert activity_lines["status_counts"]["CREATE_FAILED"] == 1
