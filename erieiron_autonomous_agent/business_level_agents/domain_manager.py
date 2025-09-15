import uuid

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
    
    update_dns(business.domain, hosted_zone_id)
    
    add_ses_forwarding(business)


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


def add_ses_forwarding(business):
    ses = aws_utils.client("ses")
    
    rule_set_name = "erieiron-autogen"
    
    # Ensure rule set exists
    try:
        ses.describe_receipt_rule_set(RuleSetName=rule_set_name)
    except ses.exceptions.RuleSetDoesNotExistException:
        ses.create_receipt_rule_set(RuleSetName=rule_set_name)
    
    bucket_name = "erieiron-ses-inbound"
    ses.create_receipt_rule(
        RuleSetName=rule_set_name,
        Rule={
            "Name": f"store-info-{business.service_token}",
            "Enabled": True,
            "Recipients": [f"info@{business.domain}"],
            "Actions": [
                {
                    "S3Action": {
                        "BucketName": bucket_name,
                        "ObjectKeyPrefix": f"{business.service_token}/"
                    }
                }
            ],
            "ScanEnabled": True
        }
    )


def update_dns(chosen_domain, hosted_zone_id):
    route53 = aws_utils.client("route53")
    
    def upsert_record(record):
        route53.change_resource_record_sets(
            HostedZoneId=hosted_zone_id,
            ChangeBatch={
                "Changes": [record]
            }
        )
    
    # A record placeholder
    upsert_record({
        "Action": "UPSERT",
        "ResourceRecordSet": {
            "Name": chosen_domain,
            "Type": "A",
            "TTL": 300,
            "ResourceRecords": [{"Value": "127.0.0.1"}]
        }
    })
    
    # MX record for SES inbound
    upsert_record({
        "Action": "UPSERT",
        "ResourceRecordSet": {
            "Name": chosen_domain,
            "Type": "MX",
            "TTL": 300,
            "ResourceRecords": [{"Value": f"10 inbound-smtp.{aws_utils.get_aws_region()}.amazonaws.com"}]
        }
    })


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
