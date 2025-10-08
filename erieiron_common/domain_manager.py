import logging
import time
import uuid
from typing import Optional

import boto3
from botocore.exceptions import ClientError

import settings
from erieiron_autonomous_agent.models import Business
from erieiron_autonomous_agent.system_agent_llm_interface import llm_chat, get_sys_prompt
from erieiron_common import aws_utils
from erieiron_common.enums import LlmModel, LlmReasoningEffort, LlmVerbosity
from erieiron_common.llm_apis.llm_interface import LlmMessage


def manage_domain(business: Business):
    if not business.needs_domain:
        return
    
    if not business.domain:
        chosen_domain = find_domain(business)
        register_domain(chosen_domain)
        
        business.domain = chosen_domain
        business.save()
    
    hosted_zone_id = create_hosted_zone(business.domain)
    ensure_wildcard_certificate(business.domain, hosted_zone_id)
    update_dns(business.domain, hosted_zone_id)


def register_domain(chosen_domain):
    get_route53domains_client().register_domain(
        DomainName=chosen_domain,
        DurationInYears=1,
        AdminContact=settings.DOMAIN_CONTACT_INFO,
        RegistrantContact=settings.DOMAIN_CONTACT_INFO,
        TechContact=settings.DOMAIN_CONTACT_INFO,
        AutoRenew=True,
        PrivacyProtectAdminContact=True,
        PrivacyProtectRegistrantContact=True,
        PrivacyProtectTechContact=True
    )


def ensure_wildcard_certificate(chosen_domain: str, hosted_zone_id: str | None = None) -> str | None:
    """Guarantee an ACM certificate request exists and publish DNS validation records."""
    region = aws_utils.get_aws_region()
    acm = boto3.client("acm", region_name=region)
    
    root_domain = chosen_domain.rstrip('.')
    wildcard_domain = f"*.{root_domain}"
    
    def _matches(summary: dict) -> bool:
        names = {summary.get("DomainName", "")}
        names.update(summary.get("SubjectAlternativeNameSummaries", []) or [])
        normalized = {name.rstrip('.') for name in names if name}
        return root_domain in normalized and wildcard_domain in normalized
    
    paginator = acm.get_paginator("list_certificates")
    existing_cert_arn = None
    for page in paginator.paginate(CertificateStatuses=["ISSUED", "PENDING_VALIDATION", "INACTIVE"]):
        for summary in page.get("CertificateSummaryList", []) or []:
            if _matches(summary):
                existing_cert_arn = summary.get("CertificateArn")
                break
        if existing_cert_arn:
            break
    
    if existing_cert_arn:
        if hosted_zone_id:
            _ensure_certificate_dns_validation(acm, existing_cert_arn, hosted_zone_id)
        return existing_cert_arn
    
    idempotency_token = uuid.uuid4().hex[:32]
    response = acm.request_certificate(
        DomainName=wildcard_domain,
        SubjectAlternativeNames=[root_domain],
        ValidationMethod="DNS",
        IdempotencyToken=idempotency_token
    )
    certificate_arn = response.get("CertificateArn")
    
    if hosted_zone_id and certificate_arn:
        _ensure_certificate_dns_validation(acm, certificate_arn, hosted_zone_id)
    
    return certificate_arn


def _ensure_certificate_dns_validation(acm_client, certificate_arn: str, hosted_zone_id: str, *, max_attempts: int = 10, delay_seconds: float = 2.0) -> None:
    """Create/ensure DNS validation records for the ACM certificate."""
    if not certificate_arn or not hosted_zone_id:
        return
    
    validation_records = []
    for attempt in range(max_attempts):
        details = acm_client.describe_certificate(CertificateArn=certificate_arn).get("Certificate", {})
        options = details.get("DomainValidationOptions", []) or []
        validation_records = [
            option.get("ResourceRecord")
            for option in options
            if option and option.get("ValidationMethod") == "DNS" and option.get("ResourceRecord")
        ]
        if validation_records:
            break
        time.sleep(delay_seconds)
    
    if not validation_records:
        return
    
    route53 = aws_utils.client("route53")
    changes = []
    for record in validation_records:
        changes.append({
            "Action": "UPSERT",
            "ResourceRecordSet": {
                "Name": record["Name"],
                "Type": record["Type"],
                "TTL": 300,
                "ResourceRecords": [{"Value": record["Value"]}]
            }
        })
    
    if not changes:
        return
    
    route53.change_resource_record_sets(
        HostedZoneId=hosted_zone_id,
        ChangeBatch={
            "Comment": f"ACM validation for {certificate_arn}",
            "Changes": changes
        }
    )


def create_hosted_zone(chosen_domain):
    route53 = aws_utils.client("route53")
    try:
        hz_resp = route53.create_hosted_zone(
            Name=chosen_domain,
            CallerReference=str(uuid.uuid4())
        )
        hosted_zone_id = hz_resp["HostedZone"]["Id"]
    except ClientError as e:
        # If already exists, find it
        if "HostedZoneAlreadyExists" in str(e):
            zones = route53.list_hosted_zones_by_name(DNSName=chosen_domain)
            hosted_zone_id = zones["HostedZones"][0]["Id"]
        else:
            raise e
    
    return hosted_zone_id


def update_dns(chosen_domain, hosted_zone_id):
    if not chosen_domain or not hosted_zone_id:
        return

    route53 = aws_utils.client("route53")

    def upsert_record(record):
        try:
            route53.change_resource_record_sets(
                HostedZoneId=hosted_zone_id,
                ChangeBatch={"Changes": [record]}
            )
        except ClientError as exc:
            logging.warning("Unable to upsert Route53 record for %s: %s", chosen_domain, exc)

    apex = chosen_domain.rstrip('.')
    if not apex:
        return

    apex_name = f"{apex}."
    upsert_record({
        "Action": "UPSERT",
        "ResourceRecordSet": {
            "Name": apex_name,
            "Type": "A",
            "TTL": 300,
            "ResourceRecords": [{"Value": "127.0.0.1"}]
        }
    })

    upsert_record({
        "Action": "UPSERT",
        "ResourceRecordSet": {
            "Name": apex_name,
            "Type": "MX",
            "TTL": 300,
            "ResourceRecords": [{"Value": f"10 inbound-smtp.{aws_utils.get_aws_region()}.amazonaws.com"}]
        }
    })


def domain_available(domain: str) -> bool:
    resp = get_route53domains_client().check_domain_availability(
        DomainName=domain
    )
    return resp.get("Availability") == "AVAILABLE"


def get_route53domains_client():
    return boto3.client("route53domains", region_name="us-east-1")


def find_domain(business: Business):
    # Step 1: Check if Route53Domains is available
    service_token = business.service_token
    preferred_domains = [
        # f"{service_token}.com",
        # f"{service_token}.io"
    ]
    
    # for d in preferred_domains:
    #     if domain_available(d):
    #         return d
    
    unavail_domains = preferred_domains
    
    for i in range(10):
        resp = llm_chat(
            "Pick best domain name",
            [
                get_sys_prompt("domain_finder.md"),
                LlmMessage.user_from_data("Business Info", {
                    "Business Name": business.name,
                    "Business Description": business.get_latest_analysist()[0].summary
                }),
                LlmMessage.user_from_data("asdf", unavail_domains, item_name="Unavailable Domain"),
                "Please suggest domain name candidates"
            ],
            output_schema="domain_finder.md.schema.json",
            model=LlmModel.OPENAI_GPT_5,
            tag_entity=business,
            reasoning_effort=LlmReasoningEffort.HIGH,
            verbosity=LlmVerbosity.LOW
        ).json()
        
        for d in resp.get("suggested_domains"):
            if domain_available(d):
                return d
            else:
                unavail_domains.append(d)
    
    raise Exception(f"unable to find a domain for {business.service_token}")


def execute_subdomain_action(business_domain: str, sub_domain: str, action: str, comment: str):
    r53 = aws_utils.client("route53")

    hz_resp = r53.list_hosted_zones_by_name(DNSName=business_domain.rstrip('.') + ".", MaxItems="1")
    hosted_zones = hz_resp.get("HostedZones", [])
    if not hosted_zones or hosted_zones[0].get("Name", "").rstrip('.') != business_domain.rstrip('.'):
        raise Exception(f"Hosted zone for {business_domain} not found; cannot delete subdomain {sub_domain}")

    hosted_zone_id = hosted_zones[0]["Id"]
    cname_target = business_domain.rstrip('.') + "."
    r53.change_resource_record_sets(
        HostedZoneId=hosted_zone_id,
        ChangeBatch={
            "Comment": comment,
            "Changes": [
                {
                    "Action": action,
                    "ResourceRecordSet": {
                        "Name": sub_domain,
                        "Type": "CNAME",
                        "TTL": 300,
                        "ResourceRecords": [{"Value": cname_target}]
                    }
                }
            ]
        }
    )


def _delete_record_if_exists(route53_client, hosted_zone_id: str, record_name: str, record_type: str, comment: str) -> None:
    try:
        response = route53_client.list_resource_record_sets(
            HostedZoneId=hosted_zone_id,
            StartRecordName=record_name,
            StartRecordType=record_type,
            MaxItems="1"
        )
    except ClientError as exc:
        logging.warning(
            "Unable to list %s records for %s in %s: %s",
            record_type,
            record_name,
            hosted_zone_id,
            exc
        )
        return

    records = response.get("ResourceRecordSets", []) or []
    if not records:
        return

    candidate = records[0]
    if candidate.get("Type") != record_type:
        return
    if candidate.get("Name", "").rstrip('.').lower() != record_name.rstrip('.').lower():
        return

    try:
        route53_client.change_resource_record_sets(
            HostedZoneId=hosted_zone_id,
            ChangeBatch={
                "Comment": comment,
                "Changes": [
                    {
                        "Action": "DELETE",
                        "ResourceRecordSet": candidate
                    }
                ]
            }
        )
        logging.info("Deleted legacy %s record for %s in hosted zone %s", record_type, record_name, hosted_zone_id)
    except ClientError as exc:
        logging.warning(
            "Failed to delete %s record for %s in %s: %s",
            record_type,
            record_name,
            hosted_zone_id,
            exc
        )


def upsert_subdomain_alias(
        hosted_zone_id: str,
        record_name: str,
        target_dns_name: str,
        target_hosted_zone_id: str,
        *,
        comment: Optional[str] = None,
        dual_stack: bool = True
) -> None:
    """Ensure Route53 alias records point the task domain at the ALB."""
    normalized_zone_id = _normalize_hosted_zone_id(hosted_zone_id)
    if not normalized_zone_id:
        raise ValueError("hosted_zone_id is required to create alias records")

    fqdn = _ensure_fqdn(record_name)
    alias_dns = _ensure_fqdn(target_dns_name)

    route53_client = aws_utils.client("route53")

    cleanup_comment = comment or f"Cleanup prior records for {fqdn}"
    _delete_record_if_exists(route53_client, normalized_zone_id, fqdn, "CNAME", cleanup_comment)

    changes = [
        {
            "Action": "UPSERT",
            "ResourceRecordSet": {
                "Name": fqdn,
                "Type": "A",
                "AliasTarget": {
                    "DNSName": alias_dns,
                    "HostedZoneId": target_hosted_zone_id,
                    "EvaluateTargetHealth": False
                }
            }
        }
    ]

    if dual_stack:
        changes.append({
            "Action": "UPSERT",
            "ResourceRecordSet": {
                "Name": fqdn,
                "Type": "AAAA",
                "AliasTarget": {
                    "DNSName": alias_dns,
                    "HostedZoneId": target_hosted_zone_id,
                    "EvaluateTargetHealth": False
                }
            }
        })

    route53_client.change_resource_record_sets(
        HostedZoneId=normalized_zone_id,
        ChangeBatch={
            "Comment": comment or f"Auto-configured by Erie Iron for {fqdn}",
            "Changes": changes
        }
    )


def _normalize_hosted_zone_id(hosted_zone_id: Optional[str]) -> Optional[str]:
    if not hosted_zone_id:
        return None
    return str(hosted_zone_id).split("/")[-1]


def _ensure_fqdn(name: str) -> str:
    if not name:
        return name
    return name.rstrip('.') + '.'


def find_hosted_zone_id(route53_client, domain: str) -> Optional[str]:
    if not domain:
        return None
    
    labels = domain.rstrip('.').split('.')
    for idx in range(len(labels)):
        candidate = '.'.join(labels[idx:])
        dns_name = _ensure_fqdn(candidate)
        resp = route53_client.list_hosted_zones_by_name(DNSName=dns_name, MaxItems="1")
        zones = resp.get("HostedZones", [])
        if zones and zones[0].get("Name", "").rstrip('.') == candidate:
            return _normalize_hosted_zone_id(zones[0].get("Id"))
    return None


def find_certificate_arn(domain: str, region: str) -> Optional[str]:
    if not domain:
        return None
    
    acm_client = boto3.client("acm", region_name=region)
    paginator = acm_client.get_paginator("list_certificates")
    
    best_candidate: tuple[int, str] | None = None
    for page in paginator.paginate(CertificateStatuses=["ISSUED"]):
        for summary in page.get("CertificateSummaryList", []) or []:
            match_quality = _certificate_match_quality(domain, summary)
            if match_quality is None:
                continue
            candidate_arn = summary.get("CertificateArn")
            if not candidate_arn:
                continue
            if best_candidate is None or match_quality > best_candidate[0]:
                best_candidate = (match_quality, candidate_arn)
    
    if best_candidate:
        return best_candidate[1]
    return None


def _certificate_match_quality(domain: str, certificate_summary: dict) -> Optional[int]:
    potential_names = set()
    domain_name = certificate_summary.get("DomainName")
    if domain_name:
        potential_names.add(domain_name)
    for alt_name in certificate_summary.get("SubjectAlternativeNameSummaries", []) or []:
        potential_names.add(alt_name)
    
    best_match = None
    for name in potential_names:
        match_quality = _match_domain_to_cert_name(domain, name)
        if match_quality is None:
            continue
        if best_match is None or match_quality > best_match:
            best_match = match_quality
    
    return best_match


def _match_domain_to_cert_name(domain: str, cert_name: str) -> Optional[int]:
    if not domain or not cert_name:
        return None
    
    normalized_domain = domain.rstrip('.').lower()
    normalized_cert = cert_name.rstrip('.').lower()
    
    if normalized_domain == normalized_cert:
        return 200 + len(normalized_cert)
    
    if normalized_cert.startswith("*."):
        suffix = normalized_cert[1:]
        if normalized_domain.endswith(suffix) and normalized_domain != suffix.lstrip('.'):
            # prefer longer suffixes for more specific wildcards
            return 100 + len(suffix)
    
    return None


def ensure_subdomain_record(current_domain: str, zone_id: str) -> None:
    route53_client = aws_utils.client("route53")
    
    normalized_current = current_domain.rstrip('.').lower()
    parent_domain = current_domain.split('.', 1)[1]
    
    if not parent_domain:
        return
    
    normalized_parent = parent_domain.rstrip('.').lower()
    if normalized_current == normalized_parent:
        return
    
    record_name = f"{current_domain.rstrip('.')}."
    try:
        response = route53_client.list_resource_record_sets(
            HostedZoneId=zone_id,
            StartRecordName=record_name,
            StartRecordType="CNAME",
            MaxItems="1"
        )
        
        records = response.get("ResourceRecordSets", []) or []
        if records:
            first_record = records[0]
            record_name_match = first_record.get("Name", "").rstrip('.').lower() == normalized_current
            if record_name_match:
                return
    
    except ClientError as exc:
        logging.warning("Unable to verify existing Route53 record for %s: %s", current_domain, exc)
    
    execute_subdomain_action(
        parent_domain.rstrip('.'),
        current_domain.rstrip('.'),
        "UPSERT",
        f"Auto-created by Erie Iron"
    )


def delete_subdomain(domain_name):
    if not domain_name:
        return
    
    normalized_domain = domain_name.strip().rstrip('.')
    if not normalized_domain:
        return
    
    route53_client = aws_utils.client("route53")
    hosted_zone_id = find_hosted_zone_id(route53_client, normalized_domain)
    if not hosted_zone_id:
        logging.warning("Unable to locate hosted zone for %s; skipping subdomain deletion", normalized_domain)
        return
    
    try:
        hosted_zone = route53_client.get_hosted_zone(Id=hosted_zone_id)
    except ClientError as exc:
        logging.warning("Unable to fetch hosted zone %s while deleting %s: %s", hosted_zone_id, normalized_domain, exc)
    else:
        zone_name = ((hosted_zone or {}).get("HostedZone") or {}).get("Name")
        if zone_name and zone_name.rstrip('.').lower() == normalized_domain.lower():
            logging.warning("Refusing to delete apex domain record for %s", normalized_domain)
            return
    
    record_name = f"{normalized_domain}."
    try:
        response = route53_client.list_resource_record_sets(
            HostedZoneId=hosted_zone_id,
            StartRecordName=record_name,
            StartRecordType="CNAME",
            MaxItems="1"
        )
    except ClientError as exc:
        logging.warning("Unable to list Route53 record sets for %s: %s", normalized_domain, exc)
        return
    
    records = response.get("ResourceRecordSets", []) or []
    record_to_delete = None
    if records:
        candidate = records[0]
        matches_name = candidate.get("Name", "").rstrip('.').lower() == normalized_domain.lower()
        if matches_name and candidate.get("Type") == "CNAME":
            record_to_delete = candidate
    
    if not record_to_delete:
        logging.info("No matching Route53 CNAME record found for %s; nothing to delete", normalized_domain)
        return
    
    try:
        route53_client.change_resource_record_sets(
            HostedZoneId=hosted_zone_id,
            ChangeBatch={
                "Comment": f"Auto-deleted by Erie Iron for domain {normalized_domain}",
                "Changes": [
                    {
                        "Action": "DELETE",
                        "ResourceRecordSet": record_to_delete
                    }
                ]
            }
        )
        logging.info("Deleted Route53 CNAME record for %s", normalized_domain)
    except ClientError as exc:
        logging.warning("Failed to delete Route53 CNAME record for %s: %s", normalized_domain, exc)
