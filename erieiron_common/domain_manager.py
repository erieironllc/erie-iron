import hashlib
import logging
import socket
import time
import uuid
from typing import Iterable, Optional

import dns.resolver
from botocore.exceptions import ClientError
from django.db import transaction

import settings
from erieiron_autonomous_agent.models import Business, CloudAccount
from erieiron_autonomous_agent.system_agent_llm_interface import llm_chat, get_sys_prompt
from erieiron_common import aws_utils
from erieiron_common.enums import LlmModel, LlmReasoningEffort, LlmVerbosity
from erieiron_common.llm_apis.llm_interface import LlmMessage


def manage_domain(business: Business):
    if isinstance(business, Business) and getattr(business, "pk", None):
        with transaction.atomic():
            locked_business = Business.objects.select_for_update().get(pk=business.pk)
            _manage_domain_core(locked_business)
            business.domain = locked_business.domain
            business.route53_hosted_zone_id = locked_business.route53_hosted_zone_id
            return
    
    _manage_domain_core(business)


def _manage_domain_core(business: Business):
    if not business.needs_domain:
        return
    
    if not business.domain:
        cloud_account = business.get_default_cloud_account()
        chosen_domain = find_domain(business)
        register_domain(cloud_account, chosen_domain)
        
        business.domain = chosen_domain
        _persist_business_domain(business)
    
    normalized_domain = _normalize_domain_name(business.domain)
    if business.domain != normalized_domain:
        business.domain = normalized_domain
        _persist_business_domain(business)
    
    hosted_zone_id = _ensure_business_hosted_zone(business, normalized_domain)
    ensure_wildcard_certificate(business.get_default_cloud_account(), normalized_domain, hosted_zone_id)
    add_dns_records(hosted_zone_id, normalized_domain)


def register_domain(cloud_account, chosen_domain):
    route53domains_client = aws_utils.get_aws_interface(cloud_account).client("route53domains")
    
    route53domains_client.register_domain(
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


def ensure_wildcard_certificate(cloud_account: CloudAccount, chosen_domain: str, hosted_zone_id: str | None = None) -> str | None:
    """Guarantee an ACM certificate request exists and publish DNS validation records."""
    aws_interace = aws_utils.get_aws_interface(cloud_account)
    acm = aws_interace.client("acm")
    
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
    
    change_lookup: dict[tuple[str, str], dict] = {}
    for record in validation_records:
        key = (record["Name"], record["Type"])
        change_lookup[key] = {
            "Action": "UPSERT",
            "ResourceRecordSet": {
                "Name": record["Name"],
                "Type": record["Type"],
                "TTL": 300,
                "ResourceRecords": [{"Value": record["Value"]}]
            }
        }
    
    changes = list(change_lookup.values())
    
    if not changes:
        return
    
    try:
        route53.change_resource_record_sets(
            HostedZoneId=hosted_zone_id,
            ChangeBatch={
                "Comment": f"ACM validation for {certificate_arn}",
                "Changes": changes
            }
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "InvalidChangeBatch":
            raise
        
        if _validation_records_already_exist(route53, hosted_zone_id, changes):
            logging.info(
                "ACM validation records already exist for %s; continuing without changes",
                certificate_arn
            )
            return
        raise


def create_hosted_zone(chosen_domain):
    normalized_domain = _normalize_domain_name(chosen_domain)
    route53 = aws_utils.client("route53")
    
    # Reuse an existing hosted zone if one already exists; repeated stack rotations or
    # fallback domain handling should not create duplicate hosted zones in Route53.
    existing_hosted_zone_id = find_hosted_zone_id(normalized_domain, route53)
    if existing_hosted_zone_id:
        return existing_hosted_zone_id
    
    try:
        hz_resp = route53.create_hosted_zone(
            Name=_ensure_fqdn(normalized_domain),
            CallerReference=_deterministic_caller_reference(normalized_domain)
        )
        hosted_zone_id = _normalize_hosted_zone_id(hz_resp["HostedZone"]["Id"])
        _wait_for_hosted_zone_visible(route53, hosted_zone_id, normalized_domain)
    except ClientError as e:
        # If already exists, find it
        if "HostedZoneAlreadyExists" in str(e):
            hosted_zone_id = _resolve_existing_hosted_zone(route53, normalized_domain)
        else:
            raise e
    
    return hosted_zone_id


def add_dns_records(hosted_zone_id, chosen_domain):
    route53 = aws_utils.client("route53")
    
    if not chosen_domain:
        raise Exception(f"chosen_domain is required")
    
    if not hosted_zone_id:
        hosted_zone_id = find_hosted_zone_id(chosen_domain, route53)
        if not hosted_zone_id:
            raise Exception(f"hosted_zone_id is required")
    
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


def domain_available(cloud_account: CloudAccount, domain: str) -> bool:
    try:
        route53domains_client = aws_utils.get_aws_interface(cloud_account).client("route53domains")
        resp = route53domains_client.check_domain_availability(
            DomainName=domain
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code in {"UnsupportedTLD", "OperationNotSupported"}:
            logging.info("Route53Domains does not support TLD for %s: %s", domain, error_code)
            return False
        logging.warning("Failed to check domain availability for %s: %s", domain, exc)
        return False
    return resp.get("Availability") == "AVAILABLE"


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
            if domain_available(business.get_default_cloud_account(), d):
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
    """Wait until the Route53 alias resolves to the target DNS name."""


def wait_for_dns_propagation(
        domain_name: str,
        target_dns_name: str,
        timeout=20 * 60,
        interval=10
):
    logging.info(f"start wait for dns propagation {domain_name} -> {target_dns_name}")
    deadline = time.time() + timeout
    last_err = None
    
    start_time = time.time()
    while time.time() < deadline:
        try:
            try:
                answers = socket.gethostbyname_ex(domain_name)
            except Exception as e:
                try:
                    dns_answers = dns.resolver.resolve(domain_name, "A")
                    answers = (domain_name, [], [rdata.address for rdata in dns_answers])
                except Exception:
                    raise e
            target_ips = socket.gethostbyname_ex(target_dns_name)[2]
            if any(ip in target_ips for ip in answers[2]):
                return True
            logging.info(f"waiting for dns propagation {domain_name} -> {target_dns_name} ({target_ips}).  {int(time.time() - start_time)}s of max {timeout}s.  current {answers[2]}")
        except Exception as e:
            logging.exception(f"err for dns propagation {domain_name} -> {target_dns_name}: {e}")
        time.sleep(interval)
    
    raise TimeoutError(
        f"DNS alias {domain_name} did not resolve to {target_dns_name} "
        f"within {timeout} seconds. Last error: {last_err}"
    )


def _normalize_hosted_zone_id(hosted_zone_id: Optional[str]) -> Optional[str]:
    if not hosted_zone_id:
        return None
    return str(hosted_zone_id).split("/")[-1]


def _hosted_zone_id_path(hosted_zone_id: str | None) -> Optional[str]:
    if not hosted_zone_id:
        return None
    hosted_zone_id = str(hosted_zone_id)
    return hosted_zone_id if hosted_zone_id.startswith("/") else f"/hostedzone/{hosted_zone_id}"


def _validation_records_already_exist(route53_client, hosted_zone_id: str, changes: list[dict]) -> bool:
    if not changes:
        return True
    
    desired = {
        (
            change["ResourceRecordSet"].get("Name"),
            change["ResourceRecordSet"].get("Type")
        ): change
        for change in changes
    }
    
    paginator = route53_client.get_paginator("list_resource_record_sets")
    for page in paginator.paginate(HostedZoneId=hosted_zone_id):
        for record_set in page.get("ResourceRecordSets", []) or []:
            key = (record_set.get("Name"), record_set.get("Type"))
            if key not in desired:
                continue
            
            desired_records = desired[key]["ResourceRecordSet"].get("ResourceRecords", []) or []
            existing_records = record_set.get("ResourceRecords", []) or []
            
            desired_values = sorted(record.get("Value") for record in desired_records)
            existing_values = sorted(record.get("Value") for record in existing_records)
            
            if desired_values == existing_values:
                desired.pop(key)
            
            if not desired:
                return True
    
    return False


def _deterministic_caller_reference(domain_name: str) -> str:
    normalized = _normalize_domain_name(domain_name)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"erieiron-hz-{digest}"


def _ensure_fqdn(name: str) -> str:
    if not name:
        return name
    return name.rstrip('.') + '.'


def _normalize_domain_name(domain: str | None) -> str:
    return (domain or "").strip().rstrip('.').lower()


def _list_all_hosted_zones(route53_client) -> Iterable[dict]:
    marker = None
    while True:
        kwargs = {}
        if marker:
            kwargs["Marker"] = marker
        response = route53_client.list_hosted_zones(**kwargs)
        for zone in response.get("HostedZones", []) or []:
            yield zone
        if not response.get("IsTruncated"):
            break
        marker = response.get("NextMarker")


def _match_hosted_zone_id(
        zones: Iterable[dict],
        target_domain: str,
        *,
        caller_reference: str | None = None
) -> Optional[str]:
    normalized_target = _normalize_domain_name(target_domain)
    fallback_id: Optional[str] = None
    for zone in zones or []:
        name = _normalize_domain_name(zone.get("Name"))
        if name != normalized_target:
            continue
        zone_id = _normalize_hosted_zone_id(zone.get("Id"))
        if caller_reference and zone.get("CallerReference") == caller_reference:
            return zone_id
        if fallback_id is None:
            fallback_id = zone_id
    return fallback_id


def find_hosted_zone_id(domain: str, route53_client=None) -> Optional[str]:
    normalized_domain = _normalize_domain_name(domain)
    if not normalized_domain:
        return None
    
    route53_client = route53_client or aws_utils.client("route53")
    caller_ref = _deterministic_caller_reference(normalized_domain)
    
    try:
        response = route53_client.list_hosted_zones_by_name(
            DNSName=_ensure_fqdn(normalized_domain),
            MaxItems="1"
        )
        hosted_zone_id = _match_hosted_zone_id(
            response.get("HostedZones"),
            normalized_domain,
            caller_reference=caller_ref
        )
        if hosted_zone_id:
            return hosted_zone_id
    except Exception:
        ...
    
    zones_snapshot = list(_list_all_hosted_zones(route53_client))
    hosted_zone_id = _match_hosted_zone_id(
        zones_snapshot,
        normalized_domain,
        caller_reference=caller_ref
    )
    if hosted_zone_id:
        return hosted_zone_id
    
    return None


def _wait_for_hosted_zone_visible(
        route53_client,
        hosted_zone_id: str,
        domain: str, *,
        max_attempts: int = 10,
        delay_seconds: float = 1.0
) -> None:
    normalized_id = _normalize_hosted_zone_id(hosted_zone_id)
    if not normalized_id:
        return
    
    for attempt in range(max_attempts):
        if _hosted_zone_matches(route53_client, normalized_id, domain):
            return
        time.sleep(delay_seconds)
    
    logging.warning(
        "Hosted zone %s for %s was not visible after %s attempts",
        normalized_id,
        domain,
        max_attempts
    )


def _resolve_existing_hosted_zone(
        route53_client,
        domain: str,
        *,
        max_attempts: int = 20,
        delay_seconds: float = 1.0,
        max_delay_seconds: float = 8.0
) -> str:
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            hosted_zone_id = find_hosted_zone_id(domain, route53_client)
            if hosted_zone_id:
                return hosted_zone_id
        except Exception as exc:  # pragma: no cover - defensive
            last_error = exc
        time.sleep(delay_seconds)
        delay_seconds = min(delay_seconds * 1.5, max_delay_seconds)
    
    if last_error:
        raise last_error
    raise RuntimeError(f"Existing hosted zone for {domain} not found after {max_attempts} attempts")


def _ensure_business_hosted_zone(business: Business, domain: str) -> str:
    route53_client = aws_utils.client("route53")
    stored_id = _normalize_hosted_zone_id(business.route53_hosted_zone_id)
    
    if stored_id and not _hosted_zone_matches(route53_client, stored_id, domain):
        stored_id = None
    
    if not stored_id:
        stored_id = create_hosted_zone(domain)
        _persist_business_hosted_zone_id(business, stored_id)
    else:
        _persist_business_hosted_zone_id(business, stored_id, only_if_changed=True)
    return stored_id


def _hosted_zone_matches(route53_client, hosted_zone_id: str, domain: str) -> bool:
    try:
        response = route53_client.get_hosted_zone(Id=_hosted_zone_id_path(hosted_zone_id))
    except ClientError:
        return False
    
    zone_name = _normalize_domain_name(response.get("HostedZone", {}).get("Name"))
    return zone_name == _normalize_domain_name(domain)


def _persist_business_hosted_zone_id(business: Business, hosted_zone_id: str, only_if_changed: bool = False) -> None:
    normalized_id = _normalize_hosted_zone_id(hosted_zone_id)
    if not normalized_id:
        return
    
    current = _normalize_hosted_zone_id(business.route53_hosted_zone_id)
    if only_if_changed and current == normalized_id:
        return
    
    business.route53_hosted_zone_id = normalized_id
    business.save(update_fields=["route53_hosted_zone_id"])


def _persist_business_domain(business: Business) -> None:
    if not hasattr(business, "save"):
        return
    try:
        business.save(update_fields=["domain"])
    except TypeError:
        business.save()


def find_certificate_arn(cloud_account: CloudAccount, domain: str, region: str) -> Optional[str]:
    if not domain:
        return None
    
    aws_interface = aws_utils.get_aws_interface(cloud_account)
    acm_client = aws_interface.client("acm")
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
    hosted_zone_id = find_hosted_zone_id(normalized_domain, route53_client)
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
