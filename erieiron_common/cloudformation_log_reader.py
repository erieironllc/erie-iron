"""Utilities for collecting CloudFormation failure context and related AWS logs."""

import datetime
import json
import logging
import time
from collections import Counter
from datetime import timedelta
from typing import Any

import boto3

from erieiron_common import common
from erieiron_common.aws_utils import client
from erieiron_common.cloudformation_utils import get_physical_resources
from erieiron_common.date_utils import to_utc
from erieiron_common.enums import EnvironmentType, CloudformationResourceType


def read_cloudformation_stack_activity(
        env_type: EnvironmentType,
        stack_names: list[str],
        start_time: float,
        fetch_lambda_logs=False
) -> dict[str, Any]:
    """Collect CloudFormation stack events and related AWS logs since ``start_time``.

    Returns a structured dictionary that is easier for downstream LLM prompts to consume.
    The dictionary includes per-stack CloudFormation events, CloudTrail errors, and
    CloudWatch diagnostics, along with optional Lambda logs. An empty dict is returned
    when no useful activity was discovered within the deployment window.
    """
    deployment_start_datetime = to_utc(start_time)
    stack_names = common.ensure_list(stack_names)
    
    structured_activity: dict[str, Any] = {
        "deployment_window_start": deployment_start_datetime.isoformat(),
        "stacks": {}
    }
    
    for stack_name in stack_names:
        stack_activity = collect_stack_activity_sections(
            env_type.get_aws_region(),
            stack_name,
            deployment_start_datetime
        )
        if stack_activity:
            structured_activity["stacks"][stack_name] = stack_activity
    
    if fetch_lambda_logs:
        lambda_log_payload: dict[str, Any] = {
            "context": "CloudWatch Lambda logs for stack Lambda functions"
        }
        try:
            lambda_logs = extract_cloudwatch_lambda_logs(
                stack_names=stack_names,
                env_type=env_type,
                deployment_start_datetime=deployment_start_datetime
            )
            if lambda_logs:
                lambda_log_payload["logs"] = lambda_logs
        except Exception as lambda_ex:  # pragma: no cover - AWS failures should be surfaced, not fatal
            lambda_log_payload["error"] = str(lambda_ex)
        
        if len(lambda_log_payload) > 1:
            structured_activity["lambda_logs"] = lambda_log_payload
    
    if not structured_activity["stacks"] and "lambda_logs" not in structured_activity:
        return {}
    
    return structured_activity


def collect_stack_activity_sections(
        aws_region: str,
        stack_name,
        deployment_start_datetime
):
    return {
        "cloudformation": get_cloudformation_activity(
            aws_region,
            stack_name,
            deployment_start_datetime
        ),
        "cloudtrail": get_cloudtrail_errors(
            aws_region,
            deployment_start_datetime
        ),
        "cloudwatch": get_cloudwatch_errors(
            stack_name,
            deployment_start_datetime
        )
    }


def _format_stack_event(event: dict) -> str:
    """Return a compact summary string describing a CloudFormation stack event."""
    timestamp = event.get("Timestamp")
    if hasattr(timestamp, "isoformat"):
        timestamp_str = timestamp.isoformat()
    else:
        timestamp_str = str(timestamp) if timestamp is not None else ""
    
    stack_identifier = event.get("StackName") or event.get("StackId") or ""
    logical_id = event.get("LogicalResourceId") or ""
    resource_type = event.get("ResourceType") or ""
    status = event.get("ResourceStatus") or ""
    reason = event.get("ResourceStatusReason") or ""
    physical_id = event.get("PhysicalResourceId") or ""
    client_token = event.get("ClientRequestToken") or ""
    
    parts: list[str] = []
    if timestamp_str:
        parts.append(str(timestamp_str))
    if stack_identifier:
        parts.append(f"Stack: {stack_identifier}")
    if logical_id and resource_type:
        parts.append(f"{logical_id} ({resource_type})")
    elif logical_id:
        parts.append(str(logical_id))
    elif resource_type:
        parts.append(f"ResourceType: {resource_type}")
    if status:
        parts.append(f"Status: {status}")
    if reason:
        parts.append(f"Reason: {reason}")
    if physical_id:
        parts.append(f"PhysicalId: {physical_id}")
    if client_token:
        parts.append(f"Token: {client_token}")
    
    return " | ".join(parts)


def _normalize_stack_event(event: dict) -> dict[str, Any]:
    """Return a structured representation of a CloudFormation stack event."""
    timestamp = event.get("Timestamp")
    if hasattr(timestamp, "isoformat"):
        timestamp_str = timestamp.isoformat()
    elif timestamp is not None:
        timestamp_str = str(timestamp)
    else:
        timestamp_str = ""
    
    status = event.get("ResourceStatus")
    status_str = str(status) if status is not None else ""
    
    return {
        "timestamp": timestamp_str,
        "stack_name": event.get("StackName") or event.get("StackId") or "",
        "logical_id": event.get("LogicalResourceId") or "",
        "resource_type": event.get("ResourceType") or "",
        "status": status_str,
        "reason": event.get("ResourceStatusReason") or "",
        "physical_id": event.get("PhysicalResourceId") or "",
        "client_request_token": event.get("ClientRequestToken") or "",
        "is_failure": bool(status_str and "FAILED" in status_str),
        "summary": _format_stack_event(event)
    }


def get_cloudformation_activity(
        aws_region: str,
        stack_name,
        deployment_start_datetime
):
    data: dict[str, Any] = {
        "context": "CloudFormation stack events since deployment start",
        "events": [],
        "failure_events": [],
        "failed_logical_ids": [],
        "failed_resource_descriptions": [],
        "status_counts": {},
        "collection_errors": [],
        "errors": []
    }
    
    cf_client = boto3.client("cloudformation", region_name=aws_region)
    try:
        recent_events, collection_errors = collect_recent_events(
            cf_client,
            deployment_start_datetime,
            stack_name,
            set()
        )
        
        recent_events.sort(key=lambda x: (x.get('Timestamp'), x.get('StackName', '')))  # type: ignore[arg-type]
        
        status_counter: Counter[str] = Counter()
        seen_failed_ids: set[str] = set()
        
        for event in recent_events:
            normalized_event = _normalize_stack_event(event)
            if normalized_event["summary"]:
                data["events"].append(normalized_event)
            
            status = normalized_event["status"]
            if status:
                status_counter[status] += 1
            
            if normalized_event["is_failure"]:
                data["failure_events"].append(normalized_event)
                logical_id = normalized_event["logical_id"]
                if logical_id and logical_id not in seen_failed_ids:
                    data["failed_logical_ids"].append(logical_id)
                    seen_failed_ids.add(logical_id)
        
        data["status_counts"] = dict(status_counter)
        
        if data["failed_logical_ids"]:
            for logical_id in data["failed_logical_ids"][:3]:
                owning_stack = next(
                    (
                        evt.get("StackId") or evt.get("StackName")
                        for evt in recent_events
                        if evt.get("LogicalResourceId") == logical_id
                        and isinstance(evt.get("ResourceStatus"), str)
                        and "FAILED" in evt["ResourceStatus"]
                    ),
                    stack_name
                )
                try:
                    resource_details = cf_client.describe_stack_resource(
                        StackName=owning_stack,
                        LogicalResourceId=logical_id  # type: ignore[arg-type]
                    )
                    data["failed_resource_descriptions"].append({
                        "logical_resource_id": logical_id,
                        "owning_stack": owning_stack,
                        "details": resource_details
                    })
                except Exception as ex:  # pragma: no cover - AWS failures should not crash aggregation
                    data["errors"].append(f"Failed to describe resource {logical_id}: {ex}")
        
        if collection_errors:
            data["collection_errors"].extend(collection_errors)
    except Exception as event_ex:
        data["errors"].append(f"Failed to fetch stack events for {stack_name}: {event_ex}")
    
    return data


def get_cloudtrail_errors(
        aws_region: str,
        deployment_start_datetime,
):
    data = {
        "context": "Recent CloudTrail events in this region (last 15 min)",
        "events": [],
        "errors": []
    }
    
    try:
        ct_client = boto3.client("cloudtrail", region_name=aws_region)
        now = common.get_now()
        ct_query_start_time = now - datetime.timedelta(minutes=15)
        
        ct_events = ct_client.lookup_events(
            StartTime=ct_query_start_time,
            EndTime=now,
            MaxResults=100
        )
        
        recent_ct_events = [
            event for event in ct_events.get("Events", [])
            if event['EventTime'] >= deployment_start_datetime
        ]
        recent_ct_events.sort(key=lambda x: x['EventTime'])
        
        for ct_event in recent_ct_events:
            cloudtrain_event_data = json.loads(ct_event["CloudTrailEvent"])
            error_message: str = cloudtrain_event_data.get("errorMessage")
            if not error_message:
                continue
            if "No updates are to be performed" in error_message:
                continue
            normalized_error = error_message.lower()
            if normalized_error.startswith("stack with id") and normalized_error.endswith("does not exist"):
                continue
            
            event_time = ct_event.get("EventTime")
            if hasattr(event_time, "isoformat"):
                event_time_str = event_time.isoformat()
            elif event_time is not None:
                event_time_str = str(event_time)
            else:
                event_time_str = ""
            
            data["events"].append({
                "event_name": ct_event.get("EventName"),
                "event_time": event_time_str,
                "error_message": error_message,
                "event_source": cloudtrain_event_data.get("eventSource"),
                "request_parameters": cloudtrain_event_data.get("requestParameters"),
                "response_elements": cloudtrain_event_data.get("responseElements"),
                "raw_event": cloudtrain_event_data
            })
    except Exception as ct_ex:
        data["errors"].append(f"Failed to fetch CloudTrail events: {ct_ex}")
    
    return data


def get_cloudwatch_errors(stack_name, deployment_start_datetime):
    data = {
        "context": "CloudWatch logs for stack resources since deployment start",
        "time_window": {},
        "stack_logs": None,
        "ecs_task_logs": None,
        "ecs_task_stop_reasons": None,
        "cloudwatch_alarms": None,
        "alb_error_logs": None,
        "errors": []
    }
    
    effective_end_time = to_utc(time.time())
    start_epoch = max(0, int(deployment_start_datetime.timestamp()) - 60)
    end_epoch = int(effective_end_time.timestamp()) + 60
    data["time_window"] = {
        "start_epoch": start_epoch,
        "end_epoch": end_epoch
    }
    
    try:
        stack_logs = extract_cloudwatch_stack_logs_for_window(
            stack_name=stack_name,
            start_time=start_epoch,
            end_time=end_epoch
        )
        if stack_logs:
            data["stack_logs"] = stack_logs
        
        # Attempt to collect ECS task logs if relevant (for ECS service startup failures)
        ecs_task_logs = extract_cloudwatch_ecs_task_logs(
            stack_name=stack_name,
            start_time=start_epoch,
            end_time=end_epoch
        )
        if ecs_task_logs:
            data["ecs_task_logs"] = ecs_task_logs
        
        ecs_task_stop_reasons = extract_ecs_task_stop_reasons(stack_name)
        if ecs_task_stop_reasons:
            data["ecs_task_stop_reasons"] = ecs_task_stop_reasons
        
        cloudwatch_alarms = extract_cloudwatch_alarms_for_stack(stack_name)
        if cloudwatch_alarms:
            data["cloudwatch_alarms"] = cloudwatch_alarms
        
        alb_error_logs = extract_alb_error_logs(stack_name, start_epoch, end_epoch)
        if alb_error_logs:
            data["alb_error_logs"] = alb_error_logs
    
    except Exception as stack_log_ex:
        data["errors"].append(f"Failed to collect CloudWatch logs for stack resources: {stack_log_ex}")
    
    return data


def collect_recent_events(
        cf_client,
        deployment_start_datetime,
        target_stack: str,
        visited: set[str]
) -> tuple[list[dict], list[str]]:
    """Recursively gather stack events for the stack and any nested stacks."""
    local_errors: list[str] = []
    recent: list[dict] = []
    
    identifier = target_stack
    if identifier in visited:
        return recent, local_errors
    
    events: list[dict] = []
    try:
        next_token: str | None = None
        while True:
            request: dict[str, object] = {"StackName": identifier}
            if next_token:
                request["NextToken"] = next_token
            events_resp = cf_client.describe_stack_events(**request)
            page_events = events_resp.get("StackEvents", [])
            if page_events:
                events.extend(page_events)
            next_token = events_resp.get("NextToken")
            if not next_token:
                break
            # Avoid potential infinite pagination loops if AWS returns an empty page with a token
            if not page_events:
                break
    except Exception as exc:  # pragma: no cover - defensive guard for AWS API quirkiness
        local_errors.append(f"Failed to fetch stack events for {identifier}: {exc}")
        return recent, local_errors
    
    # Mark the stack as visited using both the identifier we queried and any StackId we observe
    visited.add(identifier)
    for evt in events:
        stack_id = evt.get("StackId")
        if stack_id:
            visited.add(stack_id)
    
    # Apply a 60-second buffer before deployment start to ensure we capture all relevant events
    # This matches the CloudWatch log collection buffer used in get_cloudwatch_errors:314
    buffered_start_datetime = deployment_start_datetime - timedelta(seconds=60)
    
    for evt in events:
        timestamp = evt.get("Timestamp")
        if timestamp and timestamp >= buffered_start_datetime:
            recent.append(evt)
    
    # Discover nested stacks so their failure events are included as well
    nested_stack_ids: set[str] = set()
    try:
        paginator = cf_client.get_paginator("list_stack_resources")
        for page in paginator.paginate(StackName=identifier):
            for summary in page.get("StackResourceSummaries", []):
                if summary.get("ResourceType") != "AWS::CloudFormation::Stack":
                    continue
                nested_identifier = summary.get("PhysicalResourceId") or summary.get("StackId")
                if nested_identifier and nested_identifier not in visited:
                    nested_stack_ids.add(nested_identifier)
    except Exception as nested_exc:  # pragma: no cover - best effort, do not fail entire call
        local_errors.append(f"Failed to enumerate nested stacks for {identifier}: {nested_exc}")
    
    for nested_identifier in nested_stack_ids:
        nested_events, nested_errors = collect_recent_events(
            cf_client,
            deployment_start_datetime,
            nested_identifier,
            visited
        )
        
        recent.extend(nested_events)
        local_errors.extend(nested_errors)
    
    return recent, local_errors


def extract_ecs_task_stop_reasons(stack_name: str, max_clusters: int = 10, max_services: int = 20, max_tasks: int = 30) -> str:
    """
    Extracts ECS task stop reasons for services whose names contain the stack name.
    Returns a formatted string with taskArn and reasons.
    """
    try:
        ecs = client("ecs")
    except Exception as e:
        logging.info(f"Unable to create ECS client: {e}")
        return ""
    
    try:
        clusters_resp = ecs.list_clusters()
        cluster_arns = clusters_resp.get("clusterArns", [])[:max_clusters]
    except Exception as e:
        logging.info(f"Failed to list ECS clusters: {e}")
        return ""
    
    found_service_arns = []
    cluster_for_service = {}
    for cluster_arn in cluster_arns:
        try:
            paginator = ecs.get_paginator("list_services")
            for page in paginator.paginate(cluster=cluster_arn):
                for service_arn in page.get("serviceArns", []):
                    svc_name = service_arn.split("/")[-1]
                    if stack_name in svc_name:
                        found_service_arns.append(service_arn)
                        cluster_for_service[service_arn] = cluster_arn
                        if len(found_service_arns) >= max_services:
                            break
                if len(found_service_arns) >= max_services:
                    break
        except Exception as e:
            logging.info(f"Failed to list ECS services in cluster {cluster_arn}: {e}")
            continue
        if len(found_service_arns) >= max_services:
            break
    
    if not found_service_arns:
        return ""
    
    lines = []
    for service_arn in found_service_arns:
        cluster_arn = cluster_for_service[service_arn]
        try:
            task_arns_resp = ecs.list_tasks(cluster=cluster_arn, serviceName=service_arn, desiredStatus="STOPPED")
            task_arns = task_arns_resp.get("taskArns", [])[:max_tasks]
        except Exception as e:
            logging.info(f"Failed to list ECS tasks for service {service_arn}: {e}")
            continue
        if not task_arns:
            continue
        try:
            desc = ecs.describe_tasks(cluster=cluster_arn, tasks=task_arns)
            for task in desc.get("tasks", []):
                task_arn = task.get("taskArn", "")
                stopped_reason = task.get("stoppedReason", "")
                containers = task.get("containers", [])
                lines.append(f"Task: {task_arn}")
                if stopped_reason:
                    lines.append(f"  stoppedReason: {stopped_reason}")
                for cont in containers:
                    cname = cont.get("name", "")
                    exit_code = cont.get("exitCode")
                    reason = cont.get("reason", "")
                    if exit_code is not None or reason:
                        lines.append(f"    Container: {cname} exitCode={exit_code} reason={reason}")
        except Exception as e:
            logging.info(f"Failed to describe ECS tasks for service {service_arn}: {e}")
            continue
    return "\n".join(lines) if lines else ""


def extract_cloudwatch_alarms_for_stack(stack_name: str) -> str:
    """
    Returns CloudWatch alarms in ALARM state whose names or ARNs contain the stack name.
    """
    try:
        cloudwatch = client("cloudwatch")
    except Exception as e:
        logging.info(f"Unable to create CloudWatch client: {e}")
        return ""
    try:
        resp = cloudwatch.describe_alarms(StateValue='ALARM')
    except Exception as e:
        logging.info(f"Failed to describe CloudWatch alarms: {e}")
        return ""
    alarms = resp.get("MetricAlarms", []) + resp.get("CompositeAlarms", [])
    found = []
    for alarm in alarms:
        name = alarm.get("AlarmName", "")
        arn = alarm.get("AlarmArn", "")
        state_reason = alarm.get("StateReason", "")
        if stack_name in name or stack_name in arn:
            found.append(f"{name} — {state_reason}")
    return "\n".join(found) if found else ""


def extract_alb_error_logs(stack_name: str, start_time: int, end_time: int, max_groups: int = 10) -> str:
    """
    Searches CloudWatch log groups for ALB logs (log group names starting with
    /aws/elasticloadbalancing or /aws/applicationloadbalancer) and queries for entries
    containing "error" or "5xx" in message fields, for groups that contain the stack name.
    """
    try:
        logs = client("logs")
    except Exception as e:
        logging.info(f"Unable to create CloudWatch logs client: {e}")
        return ""
    log_groups = []
    next_token = None
    prefixes = ["/aws/elasticloadbalancing", "/aws/applicationloadbalancer"]
    try:
        for prefix in prefixes:
            next_token = None
            while True:
                kwargs = {"logGroupNamePrefix": prefix}
                if next_token:
                    kwargs["nextToken"] = next_token
                resp = logs.describe_log_groups(**kwargs)
                for lg in resp.get("logGroups", []):
                    name = lg.get("logGroupName")
                    if name and stack_name in name:
                        log_groups.append(name)
                        if len(log_groups) >= max_groups:
                            break
                if len(log_groups) >= max_groups or not resp.get("nextToken"):
                    break
                next_token = resp.get("nextToken")
    except Exception as e:
        logging.info(f"Failed to list ALB log groups: {e}")
        return ""
    if not log_groups:
        return ""
    query_str = (
        "fields @timestamp, @log, @message "
        "| filter @message like /error|5[0-9][0-9]/ "
        "| sort @timestamp asc "
        "| limit 1000"
    )
    combined = []
    batch_size = 5
    for i in range(0, len(log_groups), batch_size):
        batch = log_groups[i:i + batch_size]
        try:
            q = logs.start_query(
                logGroupNames=batch,
                startTime=int(start_time),
                endTime=int(end_time),
                queryString=query_str
            )
            query_id = q["queryId"]
        except Exception as e:
            logging.info(f"start_query failed for ALB log group batch {batch}: {e}")
            continue
        # Poll for completion
        status = "Running"
        for _ in range(60):
            time.sleep(1)
            resp = logs.get_query_results(queryId=query_id)
            status = resp.get("status")
            if status in ("Complete", "Failed", "Cancelled", "Timeout"):
                results = resp.get("results", [])
                for item in results:
                    fields = {f.get("field"): f.get("value") for f in item}
                    ts = fields.get("@timestamp", "")
                    lg = fields.get("@log", "")
                    msg = fields.get("@message", "")
                    combined.append(f"{lg} | {ts}\n{msg}")
                break
        if status != "Complete":
            logging.info(f"Logs Insights query did not complete (status={status}) for ALB log group batch {batch}")
    return "\n\n".join(combined) if combined else ""


# Helper to extract ECS task logs for failed ECS service launches
def extract_cloudwatch_ecs_task_logs(
        stack_name: str,
        start_time: int,
        end_time: int,
        max_clusters: int = 10,
        max_services: int = 20,
        max_tasks: int = 20,
        max_log_groups: int = 20,
        max_logs_per_group: int = 1000
) -> str:
    """
    Attempts to collect ECS task-level logs and container logs from ECS services whose names contain the stack name.
    Returns formatted string of logs, or empty string if none found.
    """
    import collections
    try:
        ecs = client("ecs")
        logs = client("logs")
    except Exception as e:
        logging.info(f"Unable to create ECS/logs clients: {e}")
        return ""
    
    # List clusters
    try:
        clusters_resp = ecs.list_clusters()
        cluster_arns = clusters_resp.get("clusterArns", [])[:max_clusters]
    except Exception as e:
        logging.info(f"Failed to list ECS clusters: {e}")
        return ""
    
    found_service_arns = []
    found_service_names = []
    cluster_for_service = {}
    for cluster_arn in cluster_arns:
        try:
            paginator = ecs.get_paginator("list_services")
            for page in paginator.paginate(cluster=cluster_arn):
                for service_arn in page.get("serviceArns", []):
                    # Get the service name from ARN (last part after '/')
                    svc_name = service_arn.split("/")[-1]
                    # Broaden discovery: include all ECS services, not just those with stack_name
                    if stack_name in svc_name or svc_name.startswith("ecs") or True:
                        found_service_arns.append(service_arn)
                        found_service_names.append(svc_name)
                        cluster_for_service[service_arn] = cluster_arn
                        if len(found_service_arns) >= max_services:
                            break
                if len(found_service_arns) >= max_services:
                    break
        except Exception as e:
            logging.info(f"Failed to list ECS services in cluster {cluster_arn}: {e}")
            continue
        if len(found_service_arns) >= max_services:
            break
    
    if not found_service_arns:
        return ""
    
    all_task_arns = []
    for service_arn in found_service_arns:
        cluster_arn = cluster_for_service[service_arn]
        try:
            # List tasks for this service
            task_arns_resp = ecs.list_tasks(cluster=cluster_arn, serviceName=service_arn, desiredStatus="STOPPED")
            task_arns = task_arns_resp.get("taskArns", [])[:max_tasks]
            all_task_arns.extend((cluster_arn, arn) for arn in task_arns)
        except Exception as e:
            logging.info(f"Failed to list ECS tasks for service {service_arn}: {e}")
            continue
    
    if not all_task_arns:
        return ""
    
    # Collect log groups from tasks' definitions
    log_group_names = set()
    logs_by_group = collections.defaultdict(list)
    for cluster_arn, task_arn in all_task_arns:
        try:
            task_desc = ecs.describe_tasks(cluster=cluster_arn, tasks=[task_arn])
            for task in task_desc.get("tasks", []):
                td_arn = task.get("taskDefinitionArn")
                if not td_arn:
                    continue
                # Describe task definition to get log group
                try:
                    td_desc = ecs.describe_task_definition(taskDefinition=td_arn)
                    task_def = td_desc.get("taskDefinition", {})
                except Exception as e:
                    logging.info(f"Failed to describe ECS task definition {td_arn}: {e}")
                    continue
                for container in task_def.get("containerDefinitions", []):
                    log_config = container.get("logConfiguration") or {}
                    if log_config.get("logDriver") != "awslogs":
                        continue
                    options = log_config.get("options") or {}
                    log_group_name = options.get("awslogs-group")
                    if log_group_name:
                        log_group_names.add(log_group_name)
        except Exception as e:
            logging.info(f"Failed to describe ECS task {task_arn}: {e}")
            continue
    
    # Broaden ECS log group discovery: also include all log groups starting with /ecs/ (even if not related to stack_name)
    try:
        next_token = None
        while True:
            kwargs = {"logGroupNamePrefix": "/ecs/"}
            if next_token:
                kwargs["nextToken"] = next_token
            resp = logs.describe_log_groups(**kwargs)
            for lg in resp.get("logGroups", []):
                name = lg.get("logGroupName")
                if name and (stack_name in name or name.startswith("/ecs/")):
                    log_group_names.add(name)
                    if len(log_group_names) >= max_log_groups:
                        break
            if len(log_group_names) >= max_log_groups or not resp.get("nextToken"):
                break
            next_token = resp.get("nextToken")
    except Exception as e:
        logging.info(f"Failed to list ECS log groups: {e}")
    
    if not log_group_names:
        return ""
    
    # Query logs in these log groups in the window
    log_group_names = list(log_group_names)[:max_log_groups]
    query_str = (
        "fields @timestamp, @log, @message "
        "| sort @timestamp asc "
        f"| limit {max_logs_per_group}"
    )
    for i in range(0, len(log_group_names), 5):
        batch = log_group_names[i:i + 5]
        try:
            q = logs.start_query(
                logGroupNames=batch,
                startTime=int(start_time) - 60,
                endTime=int(end_time) + 60,
                queryString=query_str
            )
            query_id = q["queryId"]
        except Exception as e:
            logging.info(f"start_query failed for ECS log group batch {batch}: {e}")
            continue
        # Poll for completion
        status = "Running"
        for _ in range(60):
            time.sleep(1)
            resp = logs.get_query_results(queryId=query_id)
            status = resp.get("status")
            if status in ("Complete", "Failed", "Cancelled", "Timeout"):
                results = resp.get("results", [])
                for item in results:
                    fields = {f.get("field"): f.get("value") for f in item}
                    ts = fields.get("@timestamp", "")
                    lg = fields.get("@log", "")
                    msg = fields.get("@message", "")
                    logs_by_group[lg].append(f"{ts}\n{msg}")
                break
        if status != "Complete":
            logging.info(f"Logs Insights query did not complete (status={status}) for ECS log group batch {batch}")
    
    if not logs_by_group:
        return ""
    
    output_sections = []
    for group, entries in logs_by_group.items():
        output_sections.append(f"--- ECS Log Group: {group} ---\n" + "\n\n".join(entries))
    return "\n\n".join(output_sections)


def extract_cloudwatch_stack_logs_for_window(
        stack_name: str,
        start_time: int,
        end_time: int,
        max_groups: int = 50
) -> str:
    """
    Given a CloudFormation stack (resolved from the current task/env), collect CloudWatch Logs
    from relevant log groups (Lambda functions and explicit LogGroup resources) within the
    provided [start_time, end_time] window (epoch seconds). Returns a concatenated text block.
    """
    try:
        cf = client("cloudformation")
        logs = client("logs")
    except Exception as e:
        logging.error(f"Unable to create AWS clients: {e}")
        return ""
    
    from datetime import datetime, timezone
    try:
        window_start_dt = datetime.fromtimestamp(int(start_time), tz=timezone.utc)
    except Exception:
        window_start_dt = None
    
    # Collect candidate log group names from stack resources
    log_group_names = []
    ecs_task_definition_arns = set()
    ecs_service_identifiers = []
    try:
        paginator = cf.get_paginator("list_stack_resources")
        for page in paginator.paginate(StackName=stack_name):
            for r in page.get("StackResourceSummaries", []):
                rtype = r.get("ResourceType", "")
                phys = r.get("PhysicalResourceId", "")
                # Explicit log groups
                if rtype == "AWS::Logs::LogGroup" and phys:
                    # PhysicalResourceId for LogGroup can be the full name or ARN; normalize to name
                    name = phys
                    if ":log-group:" in name:
                        # ARN format: arn:aws:logs:region:acct:log-group:NAME:*
                        name = name.split(":log-group:", 1)[-1].split(":")[0]
                    if name:
                        log_group_names.append(name)
                # Lambda functions -> /aws/lambda/<function name>
                elif rtype == "AWS::Lambda::Function" and phys:
                    log_group_names.append(f"/aws/lambda/{phys}")
                elif rtype == "AWS::ECS::TaskDefinition" and phys:
                    ecs_task_definition_arns.add(phys)
                elif rtype == "AWS::ECS::Service" and phys:
                    ecs_service_identifiers.append(phys)
    except Exception as e:
        logging.info(f"Failed to enumerate stack resources for {stack_name}: {e}")
    
    service_event_records = []
    ecs_log_groups = set()
    ecs_client = None
    if ecs_task_definition_arns or ecs_service_identifiers:
        try:
            ecs_client = client("ecs")
        except Exception as e:
            logging.info(f"Failed to create ECS client: {e}")
    
    def _build_ecs_describe_kwargs(identifier: str) -> dict:
        if not identifier:
            return {"services": []}
        if identifier.startswith("arn:"):
            kwargs = {"services": [identifier]}
            try:
                after = identifier.split("service/", 1)[1]
                cluster_part = after.split("/", 1)[0]
                if cluster_part:
                    kwargs["cluster"] = cluster_part
            except Exception:
                pass
            return kwargs
        if "/" in identifier:
            parts = identifier.split("/")
            cluster_part = "/".join(parts[:-1])
            service_part = parts[-1]
            kwargs = {"services": [service_part]}
            if cluster_part:
                kwargs["cluster"] = cluster_part
            return kwargs
        return {"services": [identifier]}
    
    if ecs_client:
        for service_identifier in ecs_service_identifiers:
            kwargs = _build_ecs_describe_kwargs(service_identifier)
            if not kwargs.get("services"):
                continue
            try:
                resp = ecs_client.describe_services(**kwargs)
            except Exception as e:
                logging.info(f"Failed to describe ECS service {service_identifier}: {e}")
                continue
            for failure in resp.get("failures", []):
                failure_arn = failure.get("arn") or service_identifier
                failure_reason = failure.get("reason") or "Unknown"
                failure_detail = failure.get("detail")
                msg = f"DescribeServices failure ({failure_reason}) for {failure_arn}"
                if failure_detail:
                    msg += f": {failure_detail}"
                service_event_records.append((None, failure_arn, msg))
            for service in resp.get("services", []):
                td_arn = service.get("taskDefinition")
                if td_arn:
                    ecs_task_definition_arns.add(td_arn)
                service_name = service.get("serviceName") or service.get("serviceArn") or service_identifier
                for event in service.get("events", []) or []:
                    created_at = event.get("createdAt")
                    event_dt = None
                    if isinstance(created_at, datetime):
                        event_dt = created_at
                        if event_dt.tzinfo is None:
                            event_dt = event_dt.replace(tzinfo=timezone.utc)
                    if window_start_dt and event_dt and event_dt < window_start_dt:
                        continue
                    message = event.get("message")
                    if not message:
                        continue
                    service_event_records.append((event_dt, service_name, message))
        for task_def_arn in list(ecs_task_definition_arns):
            try:
                td_resp = ecs_client.describe_task_definition(taskDefinition=task_def_arn)
                task_def = td_resp.get("taskDefinition", {})
            except Exception as e:
                logging.info(f"Failed to describe ECS task definition {task_def_arn}: {e}")
                continue
            for container in task_def.get("containerDefinitions", []):
                log_config = container.get("logConfiguration") or {}
                if log_config.get("logDriver") != "awslogs":
                    continue
                options = log_config.get("options") or {}
                log_group_name = options.get("awslogs-group")
                if log_group_name:
                    ecs_log_groups.add(log_group_name)
    
    if ecs_log_groups:
        log_group_names.extend(sorted(ecs_log_groups))
    
    # Fallback: include lambda groups whose names contain the stack name
    if len(log_group_names) == 0:
        try:
            next_token = None
            while True:
                kwargs = {"logGroupNamePrefix": "/aws/lambda/"}
                if next_token:
                    kwargs["nextToken"] = next_token
                resp = logs.describe_log_groups(**kwargs)
                for lg in resp.get("logGroups", []):
                    name = lg.get("logGroupName")
                    if name and stack_name in name:
                        log_group_names.append(name)
                        if len(log_group_names) >= max_groups:
                            break
                if len(log_group_names) >= max_groups or not resp.get("nextToken"):
                    break
                next_token = resp.get("nextToken")
        except Exception as e:
            logging.info(f"Fallback discovery failed: {e}")
    
    # Deduplicate and clip to max_groups
    log_group_names = list(dict.fromkeys([n for n in log_group_names if isinstance(n, str) and n.strip()]))[:max_groups]
    if not log_group_names:
        return ""
    
    # Logs Insights query over the time window - no RequestId filter, just the window
    query_str = (
        "fields @timestamp, @log, @message "
        "| sort @timestamp asc "
        "| limit 2000"
    )
    
    log_results = []
    batch_size = 10
    for i in range(0, len(log_group_names), batch_size):
        batch = log_group_names[i:i + batch_size]
        try:
            q = logs.start_query(
                logGroupNames=batch,
                startTime=int(start_time),
                endTime=int(end_time),
                queryString=query_str
            )
            query_id = q["queryId"]
        except Exception as e:
            logging.info(f"start_query failed for batch {batch}: {e}")
            continue
        
        status = "Running"
        for _ in range(60):
            time.sleep(1)
            resp = logs.get_query_results(queryId=query_id)
            status = resp.get("status")
            if status in ("Complete", "Failed", "Cancelled", "Timeout"):
                results = resp.get("results", [])
                for item in results:
                    fields = {f.get("field"): f.get("value") for f in item}
                    ts = fields.get("@timestamp", "")
                    lg = fields.get("@log", "")
                    msg = fields.get("@message", "")
                    log_results.append(f"{ts}  {lg}\n{msg}")
                break
        
        if status != "Complete":
            logging.info(f"Logs Insights window query did not complete (status={status}) for batch {batch}")
    
    output_sections = []
    if service_event_records:
        default_dt = datetime.min.replace(tzinfo=timezone.utc)
        service_event_records.sort(key=lambda item: item[0] or default_dt)
        max_events = 50
        trimmed_records = service_event_records[-max_events:]
        formatted_events = []
        for dt_val, svc_name, message in trimmed_records:
            timestamp_str = dt_val.isoformat() if isinstance(dt_val, datetime) else "unknown"
            formatted_events.append(f"{timestamp_str} | ECS Service {svc_name}: {message}")
        if formatted_events:
            output_sections.append("=== ECS Service Events ===\n" + "\n".join(formatted_events))
    if log_results:
        output_sections.append("=== CloudWatch Logs ===\n" + "\n\n".join(log_results))
    if not output_sections and ecs_log_groups:
        output_sections.append(
            "Discovered ECS log groups but no log events were returned in the selected window: "
            + ", ".join(sorted(ecs_log_groups))
        )
    return "\n\n".join(output_sections)


def extract_cloudwatch_lambda_logs(
        stack_names: list[str],
        env_type: EnvironmentType,
        deployment_start_datetime: datetime.datetime,
        max_groups: int = 50
) -> str:
    """
    Fetch CloudWatch Logs Insights entries for Lambda functions provisioned by the given stacks.
    We scan logs from deployment start time (with 60-second buffer) across up to `max_groups` Lambda log groups.
    Returns a concatenated text block, sorted by timestamp, suitable for appending to error output.
    """
    try:
        lambda_resources = get_physical_resources(
            stack_names,
            env_type,
            CloudformationResourceType.LAMBDA_FUNCTION
        )
    except Exception as exc:
        logging.info(f"Failed to enumerate Lambda resources for stacks {stack_names}: {exc}")
        return ""
    
    if not lambda_resources:
        return ""
    
    log_group_candidates = set()
    for physical_id in lambda_resources.keys():
        if not isinstance(physical_id, str):
            continue
        function_name = physical_id.split(":")[-1]
        if not function_name:
            continue
        log_group_candidates.add(f"/aws/lambda/{function_name}")
    
    if not log_group_candidates:
        return ""
    
    sorted_log_groups = sorted(log_group_candidates)
    if len(sorted_log_groups) > max_groups:
        logging.info(
            "Truncating Lambda log group list from %s to %s while collecting CloudWatch logs",
            len(sorted_log_groups),
            max_groups
        )
    log_group_names = sorted_log_groups[:max_groups]
    
    logs = boto3.client("logs", region_name=env_type.get_aws_region())
    
    # Apply a 60-second buffer before deployment start to ensure we capture all relevant events
    # This matches the buffer logic used in collect_recent_events and get_cloudwatch_errors
    buffered_start_datetime = deployment_start_datetime - timedelta(seconds=60)
    effective_end_time = to_utc(time.time())
    
    start = int(buffered_start_datetime.timestamp())
    end = int(effective_end_time.timestamp()) + 60
    query_str = "fields @timestamp, @log, @message | sort @timestamp asc | limit 1000"
    
    combined = []
    batch_size = 10
    for i in range(0, len(log_group_names), batch_size):
        batch = log_group_names[i:i + batch_size]
        try:
            q = logs.start_query(
                logGroupNames=batch,
                startTime=start,
                endTime=end,
                queryString=query_str
            )
            query_id = q["queryId"]
        except Exception as e:
            logging.info(f"start_query failed for batch {batch}: {e}")
            continue
        
        status = "Running"
        for _ in range(60):
            time.sleep(1)
            resp = logs.get_query_results(queryId=query_id)
            status = resp.get("status")
            if status in ("Complete", "Failed", "Cancelled", "Timeout"):
                results = resp.get("results", [])
                for item in results:
                    fields = {f.get("field"): f.get("value") for f in item}
                    ts = fields.get("@timestamp", "")
                    lg = fields.get("@log", "")
                    msg = fields.get("@message", "")
                    combined.append(f"{ts}  {lg}\n{msg}")
                break
        
        if status != "Complete":
            logging.info(f"Logs Insights query did not complete (status={status}) for batch {batch}")
    
    return "\n\n".join(combined)
