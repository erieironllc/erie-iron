import concurrent.futures
import datetime
import json
import logging
import time
import traceback
from pathlib import Path

import boto3
import yaml

from erieiron_common import common
from erieiron_common.enums import EnvironmentType, CloudformationResourceType

STACK_STATUS_NO_STACK = "NO_STACK"
DEV_STACK_TOKEN_LENGTH = 6


class CloudFormationException(Exception):
    def __init__(self, extracted_exception: str):
        super().__init__(extracted_exception)


class CloudFormationStackObsolete(Exception):
    """Signal raised when a stack enters a delete workflow."""
    
    def __init__(self, stack_name: str, status: str):
        self.stack_name = stack_name
        self.status = status
        message = f"CloudFormation stack {stack_name} entered {status}"
        super().__init__(message)


class CloudFormationLoader(yaml.SafeLoader):
    """Lightweight loader that treats CloudFormation intrinsics as plain mappings."""
    ...


def load_cloudformation_template(template_body: str) -> dict:
    CloudFormationLoader.add_multi_constructor('!', construct_cfn_tag)
    
    try:
        loaded = yaml.load(template_body, Loader=CloudFormationLoader)
    except yaml.YAMLError as exc:
        logging.warning("Unable to parse CloudFormation template for Route53 guardrail validation: %s", exc)
        return {}
    except Exception as exc:  # pragma: no cover - defensive fallback
        logging.warning("Unexpected error while parsing CloudFormation template for guardrail validation: %s", exc)
        return {}
    
    if isinstance(loaded, dict):
        return loaded
    return {}


def construct_cfn_tag(loader, tag_suffix, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node)
    elif isinstance(node, yaml.MappingNode):
        value = loader.construct_mapping(node)
    else:
        value = None
    return {tag_suffix: value}


def template_defines_ses_mx(template_body: str) -> bool:
    template = load_cloudformation_template(template_body)
    resources = template.get("Resources") if isinstance(template, dict) else None
    if not isinstance(resources, dict):
        return False
    
    for resource in resources.values():
        if not isinstance(resource, dict):
            continue
        
        resource_type = resource.get("Type")
        properties = resource.get("Properties") or {}
        
        if resource_type == "AWS::Route53::RecordSet" and record_is_ses_mx(properties):
            return True
        
        if resource_type == "AWS::Route53::RecordSetGroup":
            record_sets = properties.get("RecordSets") or []
            if isinstance(record_sets, list):
                for record in record_sets:
                    if record_is_ses_mx(record):
                        return True
    
    return False


def record_is_ses_mx(record_props: dict) -> bool:
    if not isinstance(record_props, dict):
        return False
    
    record_type = str(record_props.get("Type", "")).upper()
    if record_type != "MX":
        return False
    
    name_expr = record_props.get("Name")
    flattened_name = flatten_cfn_expr(name_expr) or ""
    normalized_name = flattened_name.rstrip(".")
    if normalized_name not in {"${DomainName}", "DomainName"}:
        return False
    
    resource_records = record_props.get("ResourceRecords")
    if not isinstance(resource_records, list):
        return False
    
    for record in resource_records:
        flattened_record = flatten_cfn_expr(record) or ""
        if "inbound-smtp." in flattened_record and ".amazonaws.com" in flattened_record:
            return True
    
    return False


def flatten_cfn_expr(expr) -> str | None:
    if isinstance(expr, str):
        return expr
    if isinstance(expr, (int, float)):
        return str(expr)
    if isinstance(expr, dict) and len(expr) == 1:
        tag, value = next(iter(expr.items()))
        normalized_tag = tag.replace("Fn::", "")
        if normalized_tag == "Ref" and isinstance(value, str):
            return f"${{{value}}}"
        if normalized_tag == "Sub":
            if isinstance(value, str):
                return value
            if isinstance(value, list) and value:
                return flatten_cfn_expr(value[0])
        if normalized_tag == "Join" and isinstance(value, list) and len(value) == 2:
            delimiter = flatten_cfn_expr(value[0]) or ""
            sequence = []
            for item in value[1] or []:
                flattened = flatten_cfn_expr(item)
                if flattened is None:
                    return None
                sequence.append(flattened)
            return delimiter.join(sequence)
        if normalized_tag == "GetAtt":
            if isinstance(value, list):
                parts = []
                for part in value:
                    flattened = flatten_cfn_expr(part)
                    parts.append(flattened if flattened is not None else str(part))
                return ".".join(parts)
            if isinstance(value, str):
                return value
        return None
    if isinstance(expr, list):
        pieces = []
        for item in expr:
            flattened = flatten_cfn_expr(item)
            if flattened is None:
                return None
            pieces.append(flattened)
        return "".join(pieces)
    return None


def summarize_cfn_expr(expr) -> str:
    flattened = flatten_cfn_expr(expr)
    if flattened is not None:
        return flattened
    if isinstance(expr, (dict, list)):
        try:
            return json.dumps(expr, default=str)
        except TypeError:
            return str(expr)
    return str(expr)


def summarize_record_target(record_props: dict) -> str:
    alias_target = record_props.get("AliasTarget")
    if isinstance(alias_target, dict):
        dns_name = summarize_cfn_expr(alias_target.get("DNSName"))
        hosted_zone = summarize_cfn_expr(alias_target.get("HostedZoneId"))
        return f"AliasTarget(DNSName={dns_name}, HostedZoneId={hosted_zone})"
    
    resource_records = record_props.get("ResourceRecords")
    if isinstance(resource_records, list) and resource_records:
        values = [
            summarize_cfn_expr(record)
            for record in resource_records
        ]
        return f"ResourceRecords[{', '.join(values)}]"
    
    return "no target"


def is_domainname_apex(name_expr) -> bool:
    flattened = flatten_cfn_expr(name_expr)
    if not flattened:
        return False
    
    normalized = flattened.strip()
    apex_candidates = {"${DomainName}", "${DomainName}.", "DomainName", "DomainName."}
    return normalized in apex_candidates


def inspect_route53_record(identifier: str, record_props: dict, issues: list[str], alias_records: list[tuple[str, str, dict]]) -> bool:
    if not isinstance(record_props, dict):
        return False
    
    name_expr = record_props.get("Name")
    if not is_domainname_apex(name_expr):
        return False
    
    record_type = str(record_props.get("Type", "")).upper()
    target_summary = summarize_record_target(record_props)
    
    if record_type == "CNAME":
        issues.append(
            f"{identifier} creates a CNAME for {summarize_cfn_expr(name_expr)} ({target_summary})."
        )
        return True
    
    if record_type in {"A", "AAAA"}:
        alias_target = record_props.get("AliasTarget")
        if not isinstance(alias_target, dict):
            issues.append(
                f"{identifier} sets {record_type} for {summarize_cfn_expr(name_expr)} without an AliasTarget ({target_summary})."
            )
            return True
        
        alias_records.append((identifier, record_type, alias_target))
        
        dns_name = summarize_cfn_expr(alias_target.get("DNSName"))
        hosted_zone = summarize_cfn_expr(alias_target.get("HostedZoneId"))
        
        if dns_name and "DomainName" in dns_name:
            issues.append(
                f"{identifier} alias DNSName resolves to {dns_name}; point it at the Application Load Balancer DNS attribute instead."
            )
        
        if hosted_zone and hosted_zone in {"${DomainHostedZoneId}", "DomainHostedZoneId"}:
            issues.append(
                f"{identifier} alias HostedZoneId is {hosted_zone}; use the ALB CanonicalHostedZoneID attribute."
            )
        
        return True
    
    # Other record types (e.g., MX, TXT) for DomainName are allowed and do not require alias enforcement.
    return True


def enforce_route53_alias_guardrail(template_body: str) -> None:
    template = load_cloudformation_template(template_body)
    resources = template.get("Resources") if isinstance(template, dict) else None
    if not isinstance(resources, dict) or not resources:
        return
    
    issues: list[str] = []
    alias_records: list[tuple[str, str, dict]] = []
    domainname_records_present = False
    
    for logical_id, resource in resources.items():
        if not isinstance(resource, dict):
            continue
        
        resource_type = resource.get("Type")
        properties = resource.get("Properties") or {}
        
        if resource_type == "AWS::Route53::RecordSet":
            domainname_records_present |= inspect_route53_record(logical_id, properties, issues, alias_records)
        elif resource_type == "AWS::Route53::RecordSetGroup":
            record_sets = properties.get("RecordSets") or []
            if isinstance(record_sets, list):
                for idx, record in enumerate(record_sets):
                    identifier = f"{logical_id}[{idx}]"
                    domainname_records_present |= inspect_route53_record(identifier, record, issues, alias_records)
    
    if domainname_records_present and not any(record_type == "A" for _, record_type, _ in alias_records):
        issues.append("No `Type: A` alias record for `!Ref DomainName` was found. Create an AliasTarget entry pointing to the Application Load Balancer.")
    
    if issues:
        message_lines = [
            "Route53 guardrail violation: `DomainName` must use alias A/AAAA records that target the Application Load Balancer.",
            "Detected issues:",
            *[f" - {issue}" for issue in issues],
            "Update `infrastructure.yaml` so the Route53 records use `Type: A` (and optionally `AAAA`) with `AliasTarget.DNSName` and `AliasTarget.HostedZoneId` wired to the ALB attributes."
        ]
        raise Exception("\n".join(message_lines))


def get_stack_outputs(stack_name, env_type: EnvironmentType) -> dict:
    cf_client = boto3.client("cloudformation", region_name=env_type.get_aws_region())
    
    try:
        stack_descriptions = cf_client.describe_stacks(StackName=stack_name).get("Stacks")
    except cf_client.exceptions.ClientError:
        return {}
    except Exception:
        return {}
    if not stack_descriptions:
        return {}
    outputs = common.ensure_list(common.first(stack_descriptions).get("Outputs"))
    return {
        output.get("OutputKey"): output.get("OutputValue")
        for output in outputs
        if isinstance(output, dict)
    }


def cloudformation_wait(
        stack_names: list[str],
        env_type: EnvironmentType,
        *,
        timeout=20 * 60,
        poll_interval=10,
        throw_on_fail=False
):
    for stack_name in common.ensure_list(stack_names):
        _wait_for_single_stack(
            stack_name,
            env_type=env_type,
            timeout=timeout,
            poll_interval=poll_interval,
            throw_on_fail=throw_on_fail
        )


def _wait_for_single_stack(
        stack_name: str,
        env_type: EnvironmentType,
        *,
        timeout: int,
        poll_interval: int,
        throw_on_fail: bool
) -> None:
    cf_client = boto3.client("cloudformation", region_name=env_type.get_aws_region())
    ecs_client = boto3.client("ecs", region_name=env_type.get_aws_region())
    
    # Determine CloudFormation update start time
    stack_update_start = get_cloudformation_update_starttime(cf_client, stack_name)
    
    start_time = time.time()
    
    for wait_idx in range(int(timeout / poll_interval) + 1):
        stack = get_stack(stack_name, cf_client)
        if not stack:
            return True

        status = stack['StackStatus']

        if wait_idx > 0:
            time.sleep(poll_interval)
        logging.info(f"waiting on {stack_name}.  status: {status}. waiting {int(time.time() - start_time)}s out of a max wait of {timeout}s")

        if status in {"UPDATE_COMPLETE_CLEANUP_IN_PROGRESS"}:
            continue
        elif status in {"CREATE_COMPLETE", "UPDATE_COMPLETE", "ROLLBACK_COMPLETE", "DELETE_COMPLETE"}:
            break
        elif not status.endswith("_IN_PROGRESS"):
            break
        elif "ROLLBACK" in status:
            break
        elif status.startswith("DELETE"):
            break

        # Check for ECS service task failures during CloudFormation operations
        try:
            assert_no_ecs_errors(
                stack_name,
                env_type,
                stack_update_start
            )
        except Exception as e:
            if throw_on_fail:
                raise CloudFormationException(str(e))
            else:
                return
    else:
        # If we exit the loop without breaking, we've timed out
        raise CloudFormationStackObsolete(
            f"Timeout waiting for stack {stack_name} to reach a terminal state. Last status: {status}",
            status=status
        )

    logging.info(f"exiting wait with status {status}")
    if throw_on_fail:
        assert_cloudformation_stack_valid(stack_name, cf_client)


def cancel_stack_push(stack_name: str, env_type: EnvironmentType, wait_time=0):
    def _cancel_or_delete():
        try:
            time.sleep(wait_time)
            cf_client = boto3.client("cloudformation", region_name=env_type.get_aws_region())
            stack = cf_client.describe_stacks(StackName=stack_name)["Stacks"][0]
            status = stack.get("StackStatus", "")
            if status.startswith("UPDATE_"):
                logging.info(f"Cancelling update for {stack_name} (status: {status})")
                cf_client.cancel_update_stack(StackName=stack_name)
            else:
                logging.info(f"Deleting stack {stack_name} (status: {status})")
                cf_client.delete_stack(StackName=stack_name)
        except Exception as e:
            logging.warning(f"Failed to cancel or delete stack {stack_name}: {e}")
    
    logging.info(f"scheduling {stack_name} cancel.  wait_time={wait_time}")
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    executor.submit(_cancel_or_delete)


def get_new_tasks(ecs_client, service, stack_update_start, *, failed_tasks=False) -> list:
    cluster = service['clusterArn']
    
    if failed_tasks:
        task_arns = ecs_client.list_tasks(
            cluster=cluster,
            serviceName=service["serviceName"],
            desiredStatus="STOPPED"
        ).get("taskArns", [])
    else:
        task_arns = ecs_client.list_tasks(
            cluster=cluster,
            serviceName=service["serviceName"],
        ).get("taskArns", [])
    
    tasks = []
    if task_arns:
        task_desc = ecs_client.describe_tasks(cluster=cluster, tasks=task_arns)
        for task in task_desc.get("tasks", []):
            started_at = task.get('createdAt')
            if started_at and started_at >= stack_update_start:
                tasks.append(task)
    return tasks


def get_cloudformation_update_starttime(cf_client, stack_name: str):
    try:
        events = cf_client.describe_stack_events(StackName=stack_name).get("StackEvents", [])
        stack_update_start = max((e["Timestamp"] for e in events if "IN_PROGRESS" in e["ResourceStatus"]), default=time.time())
        if isinstance(stack_update_start, float):
            stack_update_start = datetime.datetime.fromtimestamp(stack_update_start)
    except Exception:
        stack_update_start = common.get_now()
    return stack_update_start


def get_ecs_services(ecs_client, stack_name: str, active_only=False) -> list:
    try:
        services = []
        for cluster_arn in ecs_client.list_clusters().get("clusterArns", []):
            service_arns = ecs_client.list_services(cluster=cluster_arn).get("serviceArns", [])
            if service_arns:
                desc = ecs_client.describe_services(cluster=cluster_arn, services=service_arns)
                services.extend(desc.get("services", []))
        
        if active_only:
            services = [
                s for s in services
                if s.get("status") == "ACTIVE"
            ]
        
        if stack_name:
            services = [
                s for s in services
                if stack_name in s.get("serviceName", "")
            ]
        
        return services
    except:
        return []


def get_stack(stack_name, cf_client):
    try:
        return common.first(cf_client.describe_stacks(StackName=stack_name)['Stacks'])
    except:
        return None


def assert_cloudformation_stack_valid(stack_name, cf_client):
    matching_stack = get_stack(stack_name, cf_client)
    if not matching_stack:
        raise CloudFormationException(f"CloudFormation stack {stack_name} doesn't exist")
    
    status = matching_stack['StackStatus']
    if "FAILED" in status or "ROLLBACK" in status:
        raise CloudFormationException(f"CloudFormation stack {stack_name} failed with status: {status}")


def get_stack_status(
        stack_name: str,
        env_type: EnvironmentType
) -> str:
    cf_client = boto3.client("cloudformation", region_name=env_type.get_aws_region())
    
    try:
        return common.first(cf_client.describe_stacks(StackName=stack_name)['Stacks'])['StackStatus']
    except:
        return STACK_STATUS_NO_STACK


def is_stack_exists(stack_names, env_type: EnvironmentType) -> bool:
    for stack_name in common.ensure_list(stack_names):
        if get_stack_status(stack_name, env_type) == STACK_STATUS_NO_STACK:
            return False
    
    return True


def is_stack_operational(stack_names, env_type: EnvironmentType) -> bool:
    for stack_name in common.ensure_list(stack_names):
        if get_stack_status(stack_name, env_type) not in ["CREATE_COMPLETE", "UPDATE_COMPLETE"]:
            return False
    
    return True


def get_stack_statuses(stack_names, env_type: EnvironmentType):
    return {
        stack_name: get_stack_status(stack_name, env_type)
        for stack_name in common.ensure_list(stack_names)
    }


def prepare_stack_for_update(*, stack_name, env_type: EnvironmentType):
    cf_client = boto3.client("cloudformation", region_name=env_type.get_aws_region())
    
    if not is_stack_exists(stack_name, env_type):
        return stack_name
    
    for i in range(3):
        try:
            status = get_stack_status(stack_name, env_type)
            if status not in [
                "CREATE_COMPLETE",
                "UPDATE_COMPLETE",
                "UPDATE_COMPLETE_CLEANUP_IN_PROGRESS",
                "DELETE_COMPLETE"
            ]:
                raise CloudFormationStackObsolete(
                    stack_name=stack_name,
                    status=status
                )
            
            cloudformation_wait(stack_name, env_type)
            return stack_name
        except cf_client.exceptions.ClientError as e:
            if "does not exist" in str(e):
                return stack_name
            else:
                logging.info(traceback.format_exc())
                raise
    
    raise CloudFormationStackObsolete(
        stack_name=stack_name,
        status=f"failed after three attempts to deploy {stack_name}"
    )


def extract_cloudformation_params(cfn_file: Path):
    data = yaml.load(cfn_file.read_text(), Loader=yaml.BaseLoader)
    param_metadata = data.get("Parameters") or {}
    
    required_params = {
        name
        for name, meta in param_metadata.items()
        if not isinstance(meta, dict) or ("Default" not in meta and "(optional)" not in str(meta.get("Description", "")).lower())
    }
    
    return required_params, param_metadata


def parse_cloudformation_yaml(cloudformation_yaml) -> dict:
    class CloudFormationLoader(yaml.SafeLoader):
        pass
    
    for tag in ["!Select", "!Ref", "!Sub", "!GetAtt", "!Join", "!If", "!Equals", "!And", "!Or", "!Not", "!FindInMap", "!ImportValue"]:
        CloudFormationLoader.add_constructor(tag, lambda loader, node: node.value)
    
    if isinstance(cloudformation_yaml, Path):
        cloudformation_yaml = cloudformation_yaml.read_text()
    
    return yaml.load(
        cloudformation_yaml,
        Loader=CloudFormationLoader
    )


def get_resource_configs(
        cloudformation_config_files: list[Path],
        resource_type: CloudformationResourceType
) -> dict:
    parsed_template_data = {}
    for cloudformation_config_file in common.ensure_list(cloudformation_config_files):
        parsed_cf = None
        if isinstance(cloudformation_config_file, dict):
            parsed_cf = cloudformation_config_file
        elif isinstance(cloudformation_config_file, Path):
            parsed_cf = parse_cloudformation_yaml(cloudformation_config_file.read_text())
        else:
            parsed_cf = parse_cloudformation_yaml(cloudformation_config_file)
        
        parsed_template_data = {
            **parsed_template_data,
            **parsed_cf
        }
    
    return {
        resource_name: resource_config
        for resource_name, resource_config in parsed_template_data.get("Resources").items()
        if resource_type.eq(resource_config.get("Type"))
    }


def get_physical_resources(
        stack_names: list[str],
        env_type: EnvironmentType,
        resource_type: CloudformationResourceType
) -> dict:
    cf_client = boto3.client("cloudformation", region_name=env_type.get_aws_region())
    
    resources = {}
    
    for stack_name in common.ensure_list(stack_names):
        paginator = cf_client.get_paginator("list_stack_resources")
        for page in paginator.paginate(StackName=stack_name):
            for resource in page.get("StackResourceSummaries", []) or []:
                if resource_type.eq(resource.get("ResourceType")):
                    resources[resource.get('PhysicalResourceId')] = resource
    
    return resources


def assert_no_ecs_errors(stack_name, env_type: EnvironmentType, stack_update_start):
    ecs_client = boto3.client("ecs", region_name=env_type.get_aws_region())
    
    for service in get_ecs_services(ecs_client, stack_name, active_only=True):
        service_name = service['serviceName']
        
        failures = [d for d in service.get("deployments", []) if d.get("rolloutState") == "FAILED"]
        if failures:
            logging.error(f"ECS deployment failed for {service_name}: {failures}.  canceling the update (takes a minute to cancel)")
            cancel_stack_push(stack_name, env_type, wait_time=60)
            raise Exception(f"ECS service {service_name} deployment failed")
        
        for task in get_new_tasks(ecs_client, service, stack_update_start, failed_tasks=True):
            last_status = task.get("lastStatus")
            stopped_reason = task.get("stoppedReason", "")
            
            if last_status in ("STOPPED", "DEPROVISIONING"):
                logging.error(f"ECS task failure detected (started after stack update): {stopped_reason}.  canceling the update (takes a minute to cancel)")
                cancel_stack_push(stack_name, env_type, wait_time=60)
                raise Exception(f"ECS service {service_name} deployment failed")
