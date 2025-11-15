"""Utilities for collecting CloudFormation failure context and related AWS logs."""
import datetime
import json
import logging
import time
from datetime import datetime, timezone
from datetime import timedelta
from typing import Any

import boto3

from erieiron_common import common
from erieiron_common.aws_utils import client
from erieiron_common.date_utils import to_utc
from erieiron_common.enums import EnvironmentType


def read_cloudwatch_stack_activity(
        env_type: EnvironmentType,
        stack_tokens: list[str],
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
    
    structured_activity: dict[str, Any] = {
        "deployment_window_start": deployment_start_datetime.isoformat(),
        "cloudtrail": get_cloudtrail_errors(
            env_type.get_aws_region(),
            deployment_start_datetime
        ),
        "cloudwatch": get_cloudwatch_content(
            stack_tokens,
            deployment_start_datetime
        )
    }
    
    return structured_activity


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
        ct_query_start_time = now - timedelta(minutes=15)
        
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
        logging.exception(ct_ex)
        data["errors"].append(f"Failed to fetch CloudTrail events: {ct_ex}")
    
    return data


def get_cloudwatch_content(stack_tokens, deployment_start_datetime):
    data = {
        "context": "CloudWatch logs for stack resources since deployment start",
        "errors": []
    }
    
    effective_end_time = to_utc(common.get_now())
    start_epoch = max(0, int(to_utc(deployment_start_datetime).timestamp()) - 60)
    end_epoch = int(effective_end_time.timestamp()) + 60
    data["time_window"] = {
        "start_epoch": start_epoch,
        "end_epoch": end_epoch
    }
    
    try:
        data["cloudwatch_logs"] = extract_cloudwatch_stack_logs_for_window(
            stack_tokens=stack_tokens,
            start_time=start_epoch,
            end_time=end_epoch
        )
        
        ecs_task_stop_reasons = extract_ecs_task_stop_reasons(stack_tokens)
        if ecs_task_stop_reasons:
            data["ecs_task_stop_reasons"] = ecs_task_stop_reasons
        
        cloudwatch_alarms = extract_cloudwatch_alarms_for_stack(stack_tokens)
        if cloudwatch_alarms:
            data["cloudwatch_alarms"] = cloudwatch_alarms
        
        alb_error_logs = extract_alb_error_logs(stack_tokens, start_epoch, end_epoch)
        if alb_error_logs:
            data["alb_error_logs"] = alb_error_logs
    
    except Exception as stack_log_ex:
        logging.exception(stack_log_ex)
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


def extract_ecs_task_stop_reasons(
        stack_names: list[str],
        max_clusters: int = 10,
        max_services: int = 20,
        max_tasks: int = 30
) -> str:
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
                    if any(stack_name in svc_name for stack_name in common.ensure_list(stack_names)):
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


def extract_cloudwatch_alarms_for_stack(stack_names: list[str]) -> str:
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
        if any(stack_name in name or stack_name in arn for stack_name in common.ensure_list(stack_names)):
            found.append(f"{name} — {state_reason}")
    return "\n".join(found) if found else ""


def extract_alb_error_logs(
        stack_names: list[str],
        start_time: int,
        end_time: int,
        max_groups: int = 10
) -> str:
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
                    if any(stack_name in name for stack_name in common.ensure_list(stack_names)):
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


def extract_cloudwatch_stack_logs_for_window(
        stack_tokens: list[str],
        start_time: int,
        end_time: int,
        max_groups: int = 50
) -> str:
    window_start_dt = datetime.fromtimestamp(int(start_time), tz=timezone.utc)
    
    log_groups = get_log_groups(stack_tokens)
    if not log_groups:
        logging.info(f"No matching log groups found for stack tokens {stack_tokens}")
        return ""
    
    # Logs Insights query over the time window - no RequestId filter, just the window
    query_str = (
        "fields @timestamp, @log, @message "
        "| sort @timestamp asc "
        "| limit 2000"
    )
    
    logs = client("logs")
    query_id = logs.start_query(
        logGroupNames=log_groups,
        startTime=int(start_time),
        endTime=int(end_time),
        queryString=query_str
    )["queryId"]
    
    status = "Running"
    log_results = []
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
        logging.info(f"Logs Insights window query did not complete (status={status}) ")
    
    return "\n".join(log_results)


def get_log_groups(stack_tokens: list[str]):
    logs = client("logs")
    log_groups = []
    next_token = None
    while True:
        kwargs = {"logGroupNamePrefix": "/"}  # or any known prefix
        if next_token:
            kwargs["nextToken"] = next_token
        resp = logs.describe_log_groups(**kwargs)
        for lg in resp.get("logGroups", []):
            name = lg["logGroupName"]
            if any(stack_token in name for stack_token in stack_tokens):
                log_groups.append(name)
        if "nextToken" not in resp:
            break
        next_token = resp["nextToken"]
    return log_groups
