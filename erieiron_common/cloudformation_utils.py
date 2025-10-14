import json
import logging

import yaml


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
