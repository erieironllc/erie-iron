import base64
import datetime
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Tuple, Optional
from urllib.parse import quote_plus
from urllib.parse import unquote_plus

import boto3
import rsa
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

import settings
from erieiron_common import common
from erieiron_common.aws_s3_local_cache import S3LocalCache
from erieiron_common.enums import ContainerPlatform, PublicPrivate

logging.getLogger("botocore.credentials").setLevel(logging.ERROR)

REGION_LOCKED_US_EAST_1_SERVICES = [
    # Route53 family (global)
    "route53",
    "route53domains",
    "route53-recovery-cluster",
    "route53-recovery-control-config",
    "route53-recovery-readiness",
    
    # CloudFront (global)
    "cloudfront",
    
    # IAM (global)
    "iam",
    
    # AWS Organizations (global)
    "organizations",
    
    # AWS SSO / IAM Identity Center (global)
    "sso",
    "sso-admin",
    "identitystore",
    
    # Support / Trusted Advisor (global)
    "support",
    "trustedadvisor",
    
    # AWS Account service (global)
    "account",
    
    # WAF Classic (global control plane)
    "waf",
    "waf-regional",
    
    # ACM for CloudFront certs must be in us-east-1
    # Normal ACM is regional, so include but you'll conditionally scope it
    "acm",
]


@dataclass(slots=True)
class SharedVpcContext:
    vpc_id: str
    cidr_block: str
    public_subnet_ids: list[str]
    private_subnet_ids: list[str]
    security_groups: Optional[dict[str, str]] = None
    nat_gateway_ids: Optional[list[str]] = None
    private_route_table_ids: Optional[list[str]] = None
    
    @property
    def rds_security_group_id(self) -> str:
        if not self.security_groups:
            raise RuntimeError("Missing security_groups metadata for shared VPC")
        rds_sg = self.security_groups.get("rds_security_group_id")
        if not rds_sg:
            raise RuntimeError("CloudAccount metadata missing rds_security_group_id")
        return rds_sg


def get_default_client(client_name) -> "boto3.session.Session.client":
    return boto3.client(client_name, region_name=get_aws_region())


def package_lambda(
        sandbox_root_dir: Path, code_file_path: str, dependencies: list[str], file_name: str
) -> Path:
    dependencies_normalized = []
    for d in dependencies:
        if "erieiron-public-common" in d:
            dependencies_normalized.append(
                "erieiron-public-common @ git+https://github.com/erieironllc/erieiron-public-common.git"
            )
        else:
            dependencies_normalized.append(d)
    dependencies = list(set(dependencies_normalized))
    
    full_lambda_path = sandbox_root_dir / code_file_path
    if not full_lambda_path.exists():
        raise FileNotFoundError(f"Lambda source file not found: {full_lambda_path}")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir_path = Path(temp_dir)
        shutil.copy(full_lambda_path, temp_dir_path / full_lambda_path.name)
        
        if dependencies:
            req_file = temp_dir_path / "requirements.txt"
            req_file.write_text("\n".join(dependencies))
            logging.info(f"building dependencies for {code_file_path}: {dependencies}")
            try:
                subprocess.run(
                    [
                        "podman",
                        "run",
                        "--rm",
                        "--platform",
                        ContainerPlatform.LAMBDA,
                        "--entrypoint",
                        "/bin/bash",
                        "-v",
                        f"{temp_dir_path}:/var/task",
                        "public.ecr.aws/lambda/python:3.11",
                        "-c",
                        (
                            "set -euxo pipefail && "
                            "yum install -y git && "
                            "pip install --no-cache-dir --only-binary=:all: "
                            "-r /var/task/requirements.txt -t /var/task && "
                            "ls -lh /var/task"
                        ),
                    ],
                    stderr=subprocess.STDOUT,
                    check=True,
                )
            except Exception as e:
                logging.exception(e)
                raise e
        
        zip_path = temp_dir_path.parent / file_name
        logging.info(
            f"Packaging Lambda: {code_file_path} → {file_name} with dependencies: {dependencies}. full path: {zip_path}"
        )
        subprocess.run(["zip", "-r", str(zip_path), "."], cwd=temp_dir_path, check=True)
        logging.info(
            f"Uploading {code_file_path} package.  {zip_path} → {file_name} ({zip_path.stat().st_size / (1024 * 1024):.2f}MB)"
        )
        
        return zip_path


def aws_console_url_from_arn(arn: str) -> str:
    match = re.match(r"arn:aws:([^:]+):([^:]*):([^:]*):(.*)", arn)
    if not match:
        raise ValueError(f"Invalid ARN: {arn}")
    
    service, region, account, resource = match.groups()
    region_qs = f"?region={region}" if region else ""
    console_base = "https://console.aws.amazon.com"
    
    if service == "s3":
        # arn:aws:s3:::my-bucket[/key]
        parts = resource.split("/", 1)
        bucket = parts[0]
        return f"https://s3.console.aws.amazon.com/s3/buckets/{bucket}"
    
    if service == "ec2":
        # arn:aws:ec2:us-west-2:123456789012:instance/i-abc
        if "instance/" in resource:
            instance_id = resource.split("instance/")[1]
            return f"{console_base}/ec2/v2/home{region_qs}#InstanceDetails:instanceId={instance_id}"
    
    if service == "iam":
        if "role/" in resource:
            role_name = resource.split("role/")[1]
            return f"{console_base}/iam/home#/roles/{role_name}"
        if "user/" in resource:
            user_name = resource.split("user/")[1]
            return f"{console_base}/iam/home#/users/{user_name}"
    
    if service == "lambda":
        func = resource.split(":")[-1]
        return f"{console_base}/lambda/home{region_qs}#/functions/{func}"
    
    if service == "rds":
        if resource.startswith("db:"):
            db = resource.split("db:")[1]
            return f"{console_base}/rds/home{region_qs}#database:id={db}"
    
    if service == "logs":
        if resource.startswith("log-group:"):
            group = resource.split("log-group:")[1].split(":")[0]
            encoded = group.replace("/", "%2F")
            return f"{console_base}/cloudwatch/home{region_qs}#logsV2:log-groups/log-group/{encoded}"
    
    if service == "ecs":
        if "cluster/" in resource:
            cluster = resource.split("cluster/")[1]
            return f"{console_base}/ecs/home{region_qs}#/clusters/{cluster}"
    
    return f"{console_base}/go/view?arn={arn}"


def get_aws_region() -> str:
    return os.getenv("AWS_REGION", "us-west-2")


def ensure_network_access(db_host: str, aws_region=settings.AWS_DEFAULT_REGION_NAME):
    rds = get_default_client("rds")
    ec2 = get_default_client("ec2")
    
    my_cidr = common.get_ip_address()
    db_instance, db_cluster = get_db_instance_and_cluster(db_host, aws_region)
    sg_ids = get_securitygroup_ids(db_cluster, db_instance)
    if not sg_ids:
        raise Exception(
            f"unable to fetch security group ids for {db_host} in region {settings.AWS_DEFAULT_REGION_NAME} "
        )
    
    for sg_id in sg_ids:
        sg_desc = ec2.describe_security_groups(GroupIds=[sg_id])["SecurityGroups"][0]
        
        has_rule = False
        for perm in sg_desc.get("IpPermissions", []):
            if (
                    perm.get("IpProtocol") == "tcp"
                    and perm.get("FromPort") == 5432
                    and perm.get("ToPort") == 5432
            ):
                if any(r.get("CidrIp") == my_cidr for r in perm.get("IpRanges", [])):
                    has_rule = True
                    break
        
        if has_rule:
            continue
        
        try:
            ec2.authorize_security_group_ingress(
                GroupId=sg_id,
                IpPermissions=[
                    {
                        "IpProtocol": "tcp",
                        "FromPort": 5432,
                        "ToPort": 5432,
                        "IpRanges": [
                            {
                                "CidrIp": my_cidr,
                                "Description": "jj laptop postgres access",
                            }
                        ],
                    }
                ],
            )
        except ClientError as ce:
            if (
                    ce.response.get("Error", {}).get("Code")
                    != "InvalidPermission.Duplicate"
            ):
                raise


def get_db_instance_and_cluster(db_host: str, aws_region: str) -> tuple:
    db_instance = None
    db_cluster = None
    
    rds = get_default_client("rds")
    paginator = rds.get_paginator("describe_db_instances")
    for page in paginator.paginate():
        for inst in page.get("DBInstances", []):
            ep = (inst.get("Endpoint") or {}).get("Address")
            if ep and ep == db_host:
                db_instance = inst
    
    paginator = rds.get_paginator("describe_db_clusters")
    for page in paginator.paginate():
        for cl in page.get("DBClusters", []):
            endpoints = set(
                filter(
                    None,
                    (
                        [
                            cl.get("Endpoint"),
                            cl.get("ReaderEndpoint"),
                            cl.get("CustomEndpoints", []),
                        ]
                        if isinstance(cl.get("CustomEndpoints"), list)
                        else [cl.get("Endpoint"), cl.get("ReaderEndpoint")]
                    ),
                )
            )
            if db_host in endpoints:
                db_cluster = cl
    
    return db_instance, db_cluster


def get_securitygroup_ids(db_cluster, db_instance):
    sg_ids = []
    if db_instance:
        sg_ids = [
            v["VpcSecurityGroupId"]
            for v in db_instance.get("VpcSecurityGroups", [])
            if v.get("VpcSecurityGroupId")
        ]
    elif db_cluster:
        sg_ids = [
            v["VpcSecurityGroupId"]
            for v in db_cluster.get("VpcSecurityGroups", [])
            if v.get("VpcSecurityGroupId")
        ]
    return sg_ids


def ensure_iam_role_exists_and_get_arn(role_name: str) -> str:
    iam = boto3.client("iam")
    
    try:
        role = iam.get_role(RoleName=role_name)["Role"]
        return role["Arn"]
    except iam.exceptions.NoSuchEntityException:
        pass
    
    role = iam.create_role(
        **{
            "RoleName": role_name,
            "Description": f"Erie Iron role for {role_name}",
            "AssumeRolePolicyDocument": json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        },
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        },
                    ],
                },
                indent=4,
            ),
        }
    )["Role"]
    
    iam.attach_role_policy(
        RoleName=role_name,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
    )
    
    for _ in range(30):
        try:
            role = iam.get_role(RoleName=role_name)["Role"]
            return role["Arn"]
        except Exception:
            time.sleep(1)
    
    raise Exception(f"timed out creating role {role_name}")


def sanitize_aws_name(name: str, max_length: int = 128) -> str:
    if not name:
        raise Exception("no name supplied")
    
    if common.is_list_like(name):
        name = common.safe_join(name, "-")
    
    name = str(name)
    name = name.replace("_", "-").replace(" ", "-")
    name = re.sub(r"[^A-Za-z0-9-]", "-", name)
    name = re.sub(r"-+", "-", name)
    name = name.strip("-").lower()
    
    if not name:
        name = "t"
    
    if len(name) > max_length:
        parts = name.split("-")
        if len(parts) == 1:
            name = name[:max_length]
        else:
            lengths = [len(p) for p in parts]
            protected_count = min(2, len(lengths))
            
            while sum(lengths) + (len(parts) - 1) > max_length:
                idx = max(
                    (i for i in range(protected_count, len(lengths)) if lengths[i] > 1),
                    key=lambda i: lengths[i],
                    default=None,
                )
                if idx is None:
                    break
                lengths[idx] -= 1
            
            truncated_parts = [p[:l] for p, l in zip(parts, lengths)]
            name = "-".join(truncated_parts)
            name = re.sub(r"-+", "-", name).strip("-")
    
    if not name:
        name = "t"
    
    if not re.match(r"^[a-z]", name):
        name = f"t{name}"
    
    return name[:max_length]


def assert_account_name(required_account_name):
    sts = boto3.client("sts")
    account_id = sts.get_caller_identity()["Account"]
    
    org = boto3.client("organizations")
    resp = org.describe_account(AccountId=account_id)
    account_name = resp["Account"]["Name"]
    if account_name != required_account_name:
        raise Exception(f"Invalid Account {account_name} != {required_account_name}")


def get_asg_size(auto_scaling_group) -> Tuple[int, int, int]:
    response = get_default_client("autoscaling").describe_auto_scaling_groups(
        AutoScalingGroupNames=[auto_scaling_group.value]
    )
    
    if response["AutoScalingGroups"]:
        g = response["AutoScalingGroups"][0]
        return int(g["DesiredCapacity"]), int(g["MinSize"]), int(g["MaxSize"])
    else:
        raise ValueError(
            f"No Auto Scaling group not found for {auto_scaling_group.value}"
        )


def set_asg_desired_capacity(auto_scaling_group, count: int):
    from erieiron_common import common
    
    current_size, min_intances, max_instances = get_asg_size(auto_scaling_group)
    count = max(min_intances, count)
    count = min(max_instances, count)
    
    if count == current_size:
        return
    
    common.log_info(f"UPDATING ASG Capacity {auto_scaling_group.value} to {count}")
    response = get_default_client("autoscaling").update_auto_scaling_group(
        AutoScalingGroupName=auto_scaling_group.value, DesiredCapacity=count
    )


def set_ecs_service_tasks(cluster_name, service_name, desired_count):
    get_default_client("ecs").update_service(
        cluster=cluster_name, service=service_name, desiredCount=desired_count
    )


def get_cloudwatch_url(around_time: datetime.datetime):
    if around_time is None:
        return None
    
    logging_start_time = int(
        (around_time - datetime.timedelta(seconds=0.1)).timestamp() * 1000
    )
    logging_end_time = logging_start_time + 10000
    
    return (
        f"https://us-west-2.console.aws.amazon.com/cloudwatch/home?region=us-west-2"
        f"#logsV2:log-groups/log-group/erieiron-baremetal-logs/log-events/container-logs"
        f"$3FfilterPattern$3D$26start$3D{logging_start_time}$26end$3D{logging_end_time}"
    )


def get_secret_arn(secret_name: str):
    """Resolve and return the full ARN for a Secrets Manager secret.

    If `secret_name` is already an ARN, it is returned unchanged. Otherwise,
    this will call DescribeSecret to look up and return the ARN.
    """
    if not secret_name:
        raise ValueError("missing secret_name")
    
    # If caller passed an ARN already, just return it
    if isinstance(secret_name, str) and secret_name.startswith(
            "arn:aws:secretsmanager:"
    ):
        return secret_name
    
    try:
        resp = get_default_client("secretsmanager").describe_secret(SecretId=secret_name)
        arn = resp.get("ARN")
        if not arn:
            raise RuntimeError(f"DescribeSecret returned no ARN for {secret_name}")
        return arn
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code == "ResourceNotFoundException":
            raise ValueError(f"Secret not found: {secret_name}") from e
        raise


def put_secret(secret_name: str, val: dict):
    """
    Create or update a secret in AWS Secrets Manager.

    `secret_name` may be a Secrets Manager name or full ARN.
    `val` must be a dict and will be stored as a JSON SecretString.
    Returns a dict with arn and version_id when available.
    """
    if not isinstance(val, dict):
        raise ValueError("put_secret expects `val` to be a dict")
    
    secrets_manager = get_default_client("secretsmanager")
    
    # Debug: Log current AWS context and operation details
    try:
        import boto3
        
        current_identity = boto3.client("sts").get_caller_identity()
        region = secrets_manager.meta.region_name
        logging.info(
            f"put_secret: account={current_identity.get('Account')}, region={region}, secret={secret_name}"
        )
    except Exception as e:
        logging.warning(f"Could not get AWS identity in put_secret: {e}")
    
    secret_string = json.dumps(val)
    
    try:
        # Check if the secret exists
        try:
            describe_resp = secrets_manager.describe_secret(SecretId=secret_name)
            exists = True
            logging.info(
                f"Secret exists: {secret_name}, ARN: {describe_resp.get('ARN', 'unknown')}"
            )
        except ClientError as e:
            err = e.response.get("Error", {}).get("Code")
            if err in ("ResourceNotFoundException",):
                exists = False
                logging.info(f"Secret does not exist, will create: {secret_name}")
            else:
                logging.error(f"Error checking secret existence: {e}")
                raise
        
        if exists:
            resp = secrets_manager.put_secret_value(
                SecretId=secret_name, SecretString=secret_string
            )
            logging.info(
                f"Updated secret value for {secret_name}, Version: {resp.get('VersionId')}"
            )
        else:
            resp = secrets_manager.create_secret(
                Name=secret_name, SecretString=secret_string
            )
            logging.info(f"Created secret {secret_name}, ARN: {resp.get('ARN')}")
        
        # Normalize return
        arn = resp.get("ARN") if "ARN" in resp else resp.get("ARN", None)
        version_id = (
            resp.get("VersionId")
            if "VersionId" in resp
            else resp.get("VersionId", None)
        )
        
        return arn
    except ClientError as e:
        logging.exception(f"Failed to put secret {secret_name}: {e}")
        raise


def get_secret(secret_name: str):
    # Create a Secrets Manager client
    session = boto3.session.Session()
    client = session.client(
        service_name="secretsmanager", region_name=os.getenv("AWS_REGION", "us-west-2")
    )
    
    try:
        get_secret_value_response = client.get_secret_value(SecretId=secret_name)
    except ClientError as e:
        raise e
    else:
        # Decrypts secret using the associated KMS CMK
        # Depending on whether the secret is a string or binary, one of these fields will be populated
        if "SecretString" in get_secret_value_response:
            secret = get_secret_value_response["SecretString"]
            return json.loads(secret)
        else:
            decoded_binary_secret = base64.b64decode(
                get_secret_value_response["SecretBinary"]
            )
            return json.loads(decoded_binary_secret)


@lru_cache
def get_cloudfron_url_signing_private_key():
    response = get_default_client("secretsmanager").get_secret_value(
        SecretId=settings.CLOUDFRONT_PRIVATE_KEY_SECRET_NAME
    )
    
    return rsa.PrivateKey.load_pkcs1(response["SecretString"].encode("utf-8"))


def generate_signed_cloudfront_url(
        cloudfront_domain, s3_key, expires_in_seconds=604800
):
    url = f"https://{cloudfront_domain}/{quote_plus(s3_key)}"
    expires = int(time.time()) + expires_in_seconds
    
    policy = f"""{{"Statement":[{{"Resource":"{url}","Condition":{{"DateLessThan":{{"AWS:EpochTime":{expires}}}}}}}]}}"""
    
    private_key = get_cloudfron_url_signing_private_key()
    signature = rsa.sign(policy.encode(), private_key, "SHA-1")
    encoded_sig = quote_plus(base64.b64encode(signature).decode("utf-8"))
    
    url = f"{url}?Expires={expires}&Signature={encoded_sig}&Key-Pair-Id={settings.CLOUDFRONT_KEY_PAIR_ID}"
    
    return url


def get_objpk_from_s3key(s3_key: str):
    return s3_key.split(".")[0]


def get_filename(event):
    from erieiron_common import common
    
    record = event["Records"][0]
    bucket = common.get(record, ["s3", "bucket", "name"]).strip()
    input_file_key = unquote_plus(common.get(record, ["s3", "object", "key"])).strip()
    return bucket, input_file_key


def set_aws_interface(aws_interface):
    from erieiron_common import cache
    
    aws_interface = cache.tl_set("aws_interface", aws_interface)


def get_aws_interface(cloud_account=None) -> 'AwsInterface':
    from erieiron_common import cache
    
    aws_interface = cache.tl_get("aws_interface")
    if aws_interface is None:
        aws_interface = AwsInterface(cloud_account)
        set_aws_interface(aws_interface)
    return aws_interface


class AwsInterface:
    def __init__(self, cloud_account=None):
        from erieiron_autonomous_agent.models import CloudAccount
        
        self.cloud_account: CloudAccount = cloud_account
        self.s3_passthrough_cache = S3LocalCache(self.client("s3"))
    
    def client(self, service_name, endpoint_url=None):
        return self.cloud_account.get_service_client(
            service_name,
            endpoint_url
        )
    
    def generate_presigned_url(
            self, bucket: str, file_key: str, content_type: str, expiration_minutes=1000
    ):
        return self.client("s3").generate_presigned_url(
            "put_object",
            Params={
                "Bucket": bucket,
                "Key": file_key,
                "ContentType": content_type,
            },
            ExpiresIn=expiration_minutes * 60,
        )
    
    def assert_test_interface(self):
        raise Exception("Expecting test interface, but got real one")
    
    def get_log_events(
            self,
            log_group,
            start_date: datetime.date,
            end_date: datetime.date = None,
            filter_pattern="",
    ):
        from erieiron_common import common
        
        start_time, _ = common.date_to_epoch_ms(start_date)
        kwargs = {
            "logGroupName": log_group,
            "filterPattern": filter_pattern,
            "startTime": start_time,
        }
        
        if end_date is not None:
            _, end_time = common.date_to_epoch_ms(end_date)
            kwargs["endTime"] = end_time
        
        page_iterator = (
            self.client("logs").get_paginator("filter_log_events").paginate(**kwargs)
        )
        
        for page in page_iterator:
            for event in page["events"]:
                yield event["message"]
    
    def send_email_from_template(self, subject, sender, recipient, template_name, ctx):
        from erieiron_common import common
        
        body = common.render_template(template_name, ctx)
        
        self.send_email(subject=subject, sender=sender, recipient=recipient, body=body)
    
    def send_email(self, subject, recipient, body, sender="erieironllc@gmail.com"):
        from erieiron_common import common
        
        common.log_info(f"sending email: {subject} to {recipient}")
        
        sender = common.default_str(sender, settings.FEEDBACK_EMAIL)
        ses_client = self.client("ses")
        
        # if recipient.lower() != "erieironllc@gmail.com":
        #     self.send_email(f"ccme: {subject} ({recipient})", "erieironllc@gmail.com", body)
        
        text_body = body
        
        # The HTML body of the email.
        if "<html" in body:
            html_body = body
        else:
            html_body = f"""
<html>
<head></head>
<body>{body}</body>
</html>
"""
        
        if settings.DISABLE_EMAIL_SEND:
            logging.info(
                f"""
NOT SENDING THIS EMAIL (settings.DISABLE_EMAIL_SEND=False)

TO:
{recipient}

FROM:
{sender}

SUBJECT:
{subject}

BODY:
{body}

            """
            )
            return
        
        ses_client.send_email(
            Source=sender,
            Destination={
                "ToAddresses": [
                    recipient,
                ]
            },
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": text_body, "Charset": "UTF-8"},
                    "Html": {"Data": html_body, "Charset": "UTF-8"},
                },
            },
        )
    
    def object_exists(self, bucket: str, key: str):
        try:
            self.assert_object_exists(bucket, key)
            return True
        except:
            return False
    
    def assert_object_exists(self, bucket: str, key: str):
        self.client("s3").head_object(Bucket=bucket, Key=key)
    
    def create_empty_file(self, bucket: str, key: str) -> str:
        self.client("s3").put_object(Bucket=bucket, Key=key)
    
    def delete(self, bucket: str, key: str) -> str:
        self.client("s3").delete_object(Bucket=bucket, Key=key)
    
    def download(self, bucket: str, key: str) -> str:
        return str(self.s3_passthrough_cache.get_file(bucket, key))
    
    def download_all(self, dest_dir: Path, bucket_name, prefix):
        dest_dir = Path(dest_dir).expanduser()
        dest_dir.mkdir(parents=True, exist_ok=True)
        s3 = self.client("s3")
        
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                # skip directories
                if key.endswith("/"):
                    continue
                
                s3.download_file(bucket_name, key, dest_dir / key.split("/")[-1])
    
    def send_client_message(self, person, message_type, payload):
        from erieiron_common.json_encoder import ErieIronJSONEncoder
        from erieiron_common import common
        
        if not person:
            raise ValueError("missing person")
        if not message_type:
            raise ValueError("missing message type")
        payload = payload or {}
        
        apigateway_client = self.client(
            "apigatewaymanagementapi",
            endpoint_url=f"https://{settings.CLIENT_MESSAGE_WEBSOCKET_ENDPOINT}",
        )
        
        table = boto3.resource(
            "dynamodb", region_name=settings.AWS_DEFAULT_REGION_NAME
        ).Table(settings.CLIENT_MESSAGE_DYNAMO_TABLE)
        
        # query for all connections open for this person
        response = table.query(
            IndexName="person_id-index",
            KeyConditionExpression=boto3.dynamodb.conditions.Key("person_id").eq(
                str(person.pk)
            ),
        )
        
        # send message to all connections
        for item in response.get("Items", []):
            connection_id = item["connection_id"]
            try:
                apigateway_client.post_to_connection(
                    ConnectionId=connection_id,
                    Data=json.dumps(
                        {"message_type": message_type, "payload": payload},
                        cls=ErieIronJSONEncoder,
                    ),
                )
                common.log_debug(
                    f"Websocket {message_type} message sent to connection: {connection_id}"
                )
            except ClientError as e:
                if e.response["Error"]["Code"] == "GoneException":
                    common.log_debug(
                        f"Websocket connection {connection_id} no longer exists."
                    )
                    
                    try:
                        table.delete_item(Key={"connection_id": connection_id})
                    except Exception as e2:
                        common.log_error(
                            f"failed to delete connection {connection_id} {e2}"
                        )
                else:
                    # Handle other potential exceptions
                    raise e
    
    def delete_websocket_connection(self, connection_id):
        from erieiron_common import common
        
        table = boto3.resource("dynamodb").Table("client-messages-db")
        
        response = table.query(
            IndexName="connection_id-index",
            KeyConditionExpression=boto3.dynamodb.conditions.Key("connection_id").eq(
                connection_id
            ),
        )
        
        response = table.delete_item(
            Key={"connection_id": connection_id}  # Primary key field and value
        )
        
        items = response.get("Items", [])
        
        common.log_info(
            f"Found {len(items)} items to delete for connection_id={connection_id}"
        )
        
        for item in items:
            table.delete_item(
                Key={"person_id": item["person_id"], "connection_id": connection_id}
            )
            common.log_info(
                f'Deleted item with primary key: {item["person_id"], item["connection_id"]}'
            )
        
        common.log_info("Deletion complete.")
    
    def iterate_s3_bucket_keys(self, bucket_name):
        s3 = self.client("s3")
        
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket_name):
            for obj in page.get("Contents", []):
                yield obj["Key"]
    
    def upload_file(
            self,
            file_path: str,
            dest_bucket: str,
            dest_key: str,
            content_type: str = None,
            asyncronous: bool = False,
            delete_when_done: bool = False,
    ):
        if not os.path.exists(file_path):
            raise Exception(f"{file_path} does not exist")
        
        from erieiron_common import common
        
        name = common.get(file_path, "name", str(file_path))
        common.log_debug(f"uploading {name} to s3://{dest_bucket}/{dest_key}")
        
        if asyncronous:
            threading.Thread(
                target=self._upload_file,
                args=(file_path, dest_bucket, dest_key, content_type, delete_when_done),
            ).start()
        else:
            self._upload_file(
                file_path, dest_bucket, dest_key, content_type, delete_when_done
            )
            common.log_debug(f"uploaded {name} to s3://{dest_bucket}/{dest_key}")
    
    def _upload_file(
            self,
            file_path: str,
            dest_bucket: str,
            dest_key: str,
            content_type: str,
            delete_when_done: bool,
    ):
        self.s3_passthrough_cache.put_file(
            file_path=file_path,
            bucket_name=dest_bucket,
            key=dest_key,
            content_type=content_type,
        )
        
        # if delete_when_done:
        #     from erieiron_common.common import quietly_delete
        #     quietly_delete(file_path)
    
    @lru_cache(maxsize=1)
    def get_shared_vpc(self) -> SharedVpcContext:
        """Load shared VPC metadata from the associated CloudAccount."""
        
        if not self.cloud_account:
            raise RuntimeError(
                "AwsInterface requires a CloudAccount instance to resolve VPC metadata"
            )
        
        if not self.cloud_account.has_vpc_config():
            raise RuntimeError(
                f"CloudAccount {self.cloud_account.id} is missing VPC metadata."
                " Run the target account bootstrap (phase 2) or populate"
                " CloudAccount.metadata['vpc'] before deploying."
            )
        
        vpc_config = self.cloud_account.get_vpc_config()
        if not vpc_config:
            raise RuntimeError(
                f"CloudAccount {self.cloud_account.id} has invalid VPC metadata."
                " Ensure metadata['vpc'] includes vpc_id, cidr_block,"
                " public_subnets, private_subnets, and security_groups."
            )
        
        self._hydrate_subnet_metadata(vpc_config)
        
        security_groups = vpc_config.get("security_groups") or {}
        if not security_groups.get("rds_security_group_id"):
            raise RuntimeError(
                f"CloudAccount {self.cloud_account.id} metadata is missing"
                " security_groups.rds_security_group_id"
            )
        
        public_subnet_ids = self._extract_subnet_ids(vpc_config, PublicPrivate.PUBLIC)
        private_subnet_ids = self._extract_subnet_ids(vpc_config, PublicPrivate.PRIVATE)
        
        # Extract NAT gateway configuration
        nat_config = vpc_config.get("nat_gateway_config", {})
        nat_gateway_ids = nat_config.get("nat_gateway_ids", [])
        private_route_table_ids = nat_config.get("private_route_table_ids", [])
        
        logging.info(
            "Using VPC %s from CloudAccount %s metadata",
            vpc_config.get("vpc_id"),
            self.cloud_account.id,
        )
        
        return SharedVpcContext(
            vpc_id=vpc_config["vpc_id"],
            cidr_block=vpc_config["cidr_block"],
            public_subnet_ids=public_subnet_ids,
            private_subnet_ids=private_subnet_ids,
            security_groups=security_groups,
            nat_gateway_ids=nat_gateway_ids,
            private_route_table_ids=private_route_table_ids,
        )
    
    def configure_nat_gateway(self):
        ec2 = self.client("ec2")
        
        vpc_config = self.cloud_account.get_vpc_config()
        
        # Extract NAT gateway configuration
        private_subnet_ids = self._extract_subnet_ids(vpc_config, PublicPrivate.PRIVATE)
        if not private_subnet_ids:
            # private_subnet_ids = self.get_private_subnet_ids(vpc_config)
            raise Exception(
                f"Cannot cofigure NAT routing  "
                "Account has no private subnets"
                "❌ System will have major problems related to networking ❌"
            )
        
        nat_gateway_id = self._extract_nat_gateway_id(vpc_config)
        if not nat_gateway_id:
            raise Exception(
                f"Cannot cofigure NAT routing  "
                "Account has no NAT Gateway "
                "❌ System will have major problems related to networking ❌"
            )
        
        private_route_table_ids = self._extract_private_route_table_ids(vpc_config)
        if not private_route_table_ids:
            raise Exception(
                f"Cannot cofigure NAT routing  "
                "nat gateway has no private_route_table_ids"
                "❌ System will have major problems related to networking ❌"
            )
        
        # Ensure the desired route table has a default route to a known NAT gateway
        for desired_rt_id in private_route_table_ids:
            rt = common.first(
                ec2.describe_route_tables(RouteTableIds=[desired_rt_id]).get(
                    "RouteTables", []
                )
            )
            if not rt:
                raise RuntimeError(f"Could not describe route table {desired_rt_id} ")
            
            has_correct_route = any(
                r.get("DestinationCidrBlock") == "0.0.0.0/0"
                and r.get("NatGatewayId") == nat_gateway_id
                for r in common.get_list(rt, "Routes")
            )
            
            if not has_correct_route:
                logging.info(
                    f"Creating or updating 0.0.0.0/0 route via NAT gateway {nat_gateway_id} "
                    f"on route table {desired_rt_id}"
                )
                try:
                    ec2.create_route(
                        RouteTableId=desired_rt_id,
                        DestinationCidrBlock="0.0.0.0/0",
                        NatGatewayId=nat_gateway_id,
                    )
                except ClientError as e:
                    err_code = e.response.get("Error", {}).get("Code")
                    if err_code == "RouteAlreadyExists":
                        ec2.replace_route(
                            RouteTableId=desired_rt_id,
                            DestinationCidrBlock="0.0.0.0/0",
                            NatGatewayId=nat_gateway_id,
                        )
                    else:
                        raise
        
        # Validate that each private subnet is associated with a route table that routes 0.0.0.0/0 through one of the NAT gateways
        for subnet_id in private_subnet_ids:
            desired_rt_id = common.first(private_route_table_ids)
            rt_assoc = common.first(ec2.describe_route_tables(
                Filters=[{"Name": "association.subnet-id", "Values": [subnet_id]}]
            ).get("RouteTables", []))
            
            if any(
                    route.get("NatGatewayId") == nat_gateway_id
                    and route.get("DestinationCidrBlock") == "0.0.0.0/0"
                    for route in common.get_list(rt_assoc, "Routes")
            ):
                # NAT Gateway properly set up - has a route that goes through the nat gateway
                continue
            
            logging.warning(
                f"Private subnet {subnet_id} is NOT routed through nat gateway {nat_gateway_id} "
                "ECS tasks in this subnet currently will NOT be able to reach ECR. "
                "Will now work to fix this situation now"
            )
            
            # ok, lets remediate.  first, let's identify the current association
            
            assoc_data = ec2.describe_route_tables(
                Filters=[{"Name": "association.subnet-id", "Values": [subnet_id]}]
            ).get("RouteTables", [])
            
            current_assoc = None
            for rt in assoc_data:
                for assoc in rt.get("Associations", []):
                    if assoc.get("SubnetId") == subnet_id and not assoc.get("Main", False):
                        current_assoc = assoc
                        break
                if current_assoc:
                    break
            
            if current_assoc:
                current_rt_id = current_assoc.get("RouteTableId")
                current_assoc_id = current_assoc.get("RouteTableAssociationId")
                
                if current_rt_id != desired_rt_id:
                    logging.info(
                        f"Disassociating subnet {subnet_id} from route table {current_rt_id} "
                        f"(association {current_assoc_id})"
                    )
                    ec2.disassociate_route_table(AssociationId=current_assoc_id)
                    
                    logging.info(
                        f"Associating subnet {subnet_id} with NAT route table {desired_rt_id}..."
                    )
                    ec2.associate_route_table(
                        SubnetId=subnet_id,
                        RouteTableId=desired_rt_id,
                    )
                    logging.info(
                        f"✅ Successfully associated subnet {subnet_id} with route table {desired_rt_id}"
                    )
                else:
                    logging.info(
                        f"Subnet {subnet_id} already associated with desired route table {desired_rt_id}"
                    )
            else:
                logging.info(
                    f"Subnet {subnet_id} has no explicit route table association. "
                    f"Associating with {desired_rt_id}..."
                )
                ec2.associate_route_table(
                    SubnetId=subnet_id,
                    RouteTableId=desired_rt_id,
                )
                logging.info(
                    f"✅ Successfully associated subnet {subnet_id} with route table {desired_rt_id}"
                )
    
    def _extract_private_route_table_ids(self, vpc_config) -> list[str]:
        nat_config = vpc_config.get("nat_gateway_config", {})
        private_route_table_ids = nat_config.get("private_route_table_ids", [])
        if private_route_table_ids:
            return private_route_table_ids
        
        # Discover private route table IDs by scanning route tables in this VPC
        ec2 = self.client("ec2")
        discovered = []
        
        rtbs = ec2.describe_route_tables(
            Filters=[{"Name": "vpc-id", "Values": [vpc_config["vpc_id"]]}]
        ).get("RouteTables", [])
        
        for rt in rtbs:
            # A private route table is one whose default route (0.0.0.0/0)
            # targets a NAT gateway instead of an IGW.
            for route in rt.get("Routes", []):
                if route.get("DestinationCidrBlock") == "0.0.0.0/0":
                    if route.get("NatGatewayId"):
                        discovered.append(rt.get("RouteTableId"))
                    break
        
        return discovered
    
    def _extract_nat_gateway_id(self, vpc_config) -> list[str]:
        nat_config = vpc_config.get("nat_gateway_config", {})
        nat_gateway_id = common.first(nat_config.get("nat_gateway_ids", []))
        
        if nat_gateway_id:
            return nat_gateway_id
        
        ec2 = self.client("ec2")
        resp = ec2.describe_nat_gateways(
            Filters=[{"Name": "vpc-id", "Values": [vpc_config["vpc_id"]]}]
        )
        for ngw in resp.get("NatGateways", []):
            if ngw.get("State") in ("available", "pending"):
                gw_id = ngw.get("NatGatewayId")
                if gw_id:
                    return gw_id
        
        return None
    
    def _extract_subnet_ids(self, vpc_config, public_private: PublicPrivate) -> list[str]:
        subnet_ids = [
            subnet_data['subnet_id']
            for subnet_data in common.get_list(
                vpc_config, 
                "public_subnets" if PublicPrivate.PUBLIC.eq(public_private) else "private_subnets"
            )
        ]
        if subnet_ids:
            return subnet_ids
        
        return [
            subnet_data['subnet_id']
            for subnet_data in self._describe_vpc_subnets(
                self.client("ec2"),
                vpc_config["vpc_id"]
            ) if subnet_data['public_private'] == public_private
        ]
    
    def _hydrate_subnet_metadata(self, vpc_config: dict) -> None:
        collections = ["public_subnets", "private_subnets"]
        needs_resolution = any(
            not subnet.get("subnet_id") or not subnet.get("availability_zone")
            for key in collections
            for subnet in vpc_config.get(key, [])
        )
        if not needs_resolution:
            return
        
        ec2_client = self.client("ec2")
        discovered_subnets = self._describe_vpc_subnets(
            ec2_client, vpc_config["vpc_id"]
        )
        
        changed = False
        for collection in collections:
            changed |= self._populate_subnet_details(
                vpc_config, collection, discovered_subnets
            )
        
        if changed:
            metadata = self.cloud_account.metadata or {}
            metadata["vpc"] = vpc_config
            self.cloud_account.metadata = metadata
            self.cloud_account.save(update_fields=["metadata"])
            logging.info(
                "Updated CloudAccount %s VPC metadata with discovered subnet details",
                self.cloud_account.id,
            )
    
    def _describe_vpc_subnets(self, ec2_client, vpc_id: str) -> list[dict]:
        try:
            response = ec2_client.describe_subnets(
                Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
            )
        except ClientError as exc:
            raise RuntimeError(
                f"Failed to describe subnets for VPC {vpc_id}: {exc}"
            ) from exc
        
        discovered = []
        for subnet in response.get("Subnets", []):
            tags = {
                tag.get("Key"): tag.get("Value")
                for tag in subnet.get("Tags", [])
                if tag.get("Key")
            }
            public_subnet = self.is_public_subnet(subnet, ec2_client)
            discovered.append(
                {
                    "public": public_subnet,
                    "public_private": PublicPrivate.from_is_public(public_subnet),
                    "subnet_id": subnet.get("SubnetId"),
                    "cidr_block": subnet.get("CidrBlock"),
                    "availability_zone": subnet.get("AvailabilityZone"),
                    "name": tags.get("Name"),
                }
            )
        return discovered
    
    def is_public_subnet(self, subnet, ec2_client=None):
        if not ec2_client:
            ec2_client = self.client("ec2")
        
        # Step 1: quick check
        if subnet.get("MapPublicIpOnLaunch"):
            return True
        
        # Step 2: authoritative check via route table
        rtbs = ec2_client.describe_route_tables(
            Filters=[{"Name": "association.subnet-id", "Values": [subnet["SubnetId"]]}]
        ).get("RouteTables", [])
        
        for rt in rtbs:
            for route in rt.get("Routes", []):
                if route.get("DestinationCidrBlock") == "0.0.0.0/0":
                    if route.get("GatewayId", "").startswith("igw-"):
                        return True
                    if route.get("NatGatewayId", "").startswith("nat-"):
                        return False
        return False
    
    def _populate_subnet_details(
            self,
            vpc_config: dict,
            collection_name: str,
            discovered_subnets: list[dict],
    ) -> bool:
        changed = False
        subnets = vpc_config.get(collection_name, [])
        if not subnets:
            return changed
        
        for subnet in subnets:
            if subnet.get("subnet_id") and subnet.get("availability_zone"):
                continue
            match = self._match_discovered_subnet(subnet, discovered_subnets)
            if not match:
                identifier = subnet.get("name") or subnet.get("cidr_block")
                raise RuntimeError(
                    f"Unable to resolve subnet metadata for {collection_name} entry {identifier}"
                )
            if not subnet.get("subnet_id") and match.get("subnet_id"):
                subnet["subnet_id"] = match["subnet_id"]
                changed = True
            if not subnet.get("availability_zone") and match.get("availability_zone"):
                subnet["availability_zone"] = match["availability_zone"]
                changed = True
            if not subnet.get("cidr_block") and match.get("cidr_block"):
                subnet["cidr_block"] = match["cidr_block"]
                changed = True
        return changed
    
    @staticmethod
    def _match_discovered_subnet(subnet_spec: dict, discovered_subnets: list[dict]):
        if subnet_spec.get("subnet_id"):
            for subnet in discovered_subnets:
                if subnet.get("subnet_id") == subnet_spec["subnet_id"]:
                    return subnet
        name = subnet_spec.get("name")
        if name:
            for subnet in discovered_subnets:
                if subnet.get("name") == name:
                    return subnet
        cidr_block = subnet_spec.get("cidr_block")
        if cidr_block:
            for subnet in discovered_subnets:
                if subnet.get("cidr_block") == cidr_block:
                    return subnet
        return None
    
    def empty_s3_bucket(self, bucket_name: str, *, delete_bucket: bool = False):
        s3_client = self.client("s3")
        
        try:
            # Check if bucket exists before trying to empty it
            s3_client.head_bucket(Bucket=bucket_name)
        except Exception as e:
            return
        
        # Empty the bucket by deleting all objects and versions
        logging.info(f"Emptying S3 bucket: {bucket_name}")
        
        # Delete all object versions and delete markers
        paginator = s3_client.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=bucket_name):
            objects_to_delete = []
            
            # Add all versions
            for version in page.get("Versions", []):
                objects_to_delete.append(
                    {"Key": version["Key"], "VersionId": version["VersionId"]}
                )
            
            # Add all delete markers
            for marker in page.get("DeleteMarkers", []):
                objects_to_delete.append(
                    {"Key": marker["Key"], "VersionId": marker["VersionId"]}
                )
            
            # Delete objects in batches
            if objects_to_delete:
                s3_client.delete_objects(
                    Bucket=bucket_name, Delete={"Objects": objects_to_delete}
                )
        
        logging.info(f"Successfully emptied S3 bucket {bucket_name}")
        
        # Delete the bucket after emptying
        if delete_bucket:
            s3_client.delete_bucket(Bucket=bucket_name)
            logging.info(f"Deleted S3 bucket {bucket_name}")
    
    def get_ecr_arn(self, ecr_repo_name, container_image_tag, region: str):
        ecr_client = self.client("ecr")
        repo_desc = ecr_client.describe_repositories(repositoryNames=[ecr_repo_name])
        return repo_desc["repositories"][0]["repositoryArn"]
    
    def get_full_image_uri(
            self, ecr_repo_name: str, container_image_tag: str, region: str
    ) -> str:
        account_id = self.client("sts").get_caller_identity()["Account"]
        
        return f"{account_id}.dkr.ecr.{region}.amazonaws.com/{ecr_repo_name}:{container_image_tag}"
    
    def tag_exists_in_ecr(
            self, repo_name: str, container_image_tag: str, region: str
    ) -> bool:
        if not container_image_tag:
            return False
        
        ecr_client = self.client("ecr")
        
        try:
            response = ecr_client.describe_images(
                repositoryName=repo_name, imageIds=[{"imageTag": container_image_tag}]
            )
            return bool(response.get("imageDetails"))
        except Exception as e:
            logging.exception(e)
        
        return False
    
    def validate_security_group_rules(self, security_group_id: str, vpc_cidr: str) -> bool:
        """Validate that ALB can reach ECS tasks through security group rules"""
        ec2_client = self.client("ec2")
        response = ec2_client.describe_security_groups(GroupIds=[security_group_id])
        
        # Check for port 8006 ingress rule
        for rule in response['SecurityGroups'][0]['IpPermissions']:
            if (rule.get('FromPort') == 8006 and
                    rule.get('ToPort') == 8006 and
                    vpc_cidr in [r['CidrIp'] for r in rule.get('IpRanges', [])]):
                logging.info("✅ Found required ALB->ECS ingress rule")
                return True
        
        raise Exception(f"❌ Missing ALB->ECS ingress rule on security group {security_group_id}")
    
    def wait_for_target_group_healthy(self, target_group_arn: str, timeout_minutes: int = 10) -> bool:
        """Wait for ECS tasks to be registered and healthy in target group"""
        import time
        
        elbv2_client = self.client("elbv2")
        start_time = time.time()
        timeout_seconds = timeout_minutes * 60
        
        while time.time() - start_time < timeout_seconds:
            try:
                response = elbv2_client.describe_target_health(TargetGroupArn=target_group_arn)
                targets = response.get('TargetHealthDescriptions', [])
                
                if not targets:
                    logging.info("No targets registered yet, waiting...")
                else:
                    healthy_targets = [t for t in targets if t['TargetHealth']['State'] == 'healthy']
                    unhealthy_targets = [t for t in targets if t['TargetHealth']['State'] != 'healthy']
                    
                    logging.info(f"Target health: {len(healthy_targets)} healthy, {len(unhealthy_targets)} unhealthy")
                    
                    if healthy_targets:
                        logging.info("✅ ECS tasks are healthy in target group")
                        return True
                    
                    # Log reasons for unhealthy targets
                    for target in unhealthy_targets:
                        state = target['TargetHealth']['State']
                        reason = target['TargetHealth'].get('Reason', 'Unknown')
                        logging.info(f"Target {target['Target']['Id']} is {state}: {reason}")
                
                time.sleep(30)
            except Exception as e:
                logging.info(f"Error checking target health: {e}")
                time.sleep(30)
        
        raise Exception(f"Targets did not become healthy within {timeout_minutes} minutes")
    
    def wait_for_ecs_service_stable(self, cluster_name: str, service_name: str, timeout_minutes: int = 2) -> bool:
        """Wait for ECS service to reach stable state"""
        ecs_client = self.client("ecs")
        
        try:
            waiter = ecs_client.get_waiter('services_stable')
            waiter.wait(
                cluster=cluster_name,
                services=[service_name],
                WaiterConfig={'delay': 30, 'maxAttempts': timeout_minutes * 2}
            )
            logging.info("✅ ECS service is stable")
            return True
        except Exception as e:
            logging.info(f"❌ ECS service did not stabilize: {e}")
            raise
    
    def diagnose_target_group_health(self, target_group_arn: str) -> None:
        """Diagnose current target group health status and provide detailed information"""
        
        try:
            response = self.client("elbv2").describe_target_health(TargetGroupArn=target_group_arn)
            targets = response.get('TargetHealthDescriptions', [])
            
            logging.info(f"🔍 Target Group Health Diagnosis for {target_group_arn}")
            
            if not targets:
                logging.info("❌ No targets registered in target group")
                logging.info("   → Check if ECS service is running and tasks have started")
                logging.info("   → Verify ECS service load balancer configuration")
                return
            
            logging.info(f"Found {len(targets)} registered targets:")
            
            for i, target in enumerate(targets, 1):
                target_id = target['Target']['Id']
                target_port = target['Target']['Port']
                health = target['TargetHealth']
                state = health['State']
                reason = health.get('Reason', 'No reason provided')
                description = health.get('Description', 'No description provided')
                
                status_icon = {
                    'healthy': '✅',
                    'unhealthy': '❌',
                    'initial': '🔄',
                    'unused': '⚪',
                    'draining': '⏳',
                    'unavailable': '🚫'
                }.get(state.lower(), '❓')
                
                logging.info(f"  {i}. Target {target_id}:{target_port}")
                logging.info(f"     Status: {status_icon} {state.upper()}")
                logging.info(f"     Reason: {reason}")
                logging.info(f"     Description: {description}")
                
                # Provide specific troubleshooting guidance
                if state.lower() == 'unhealthy':
                    logging.info(f"     🔧 Troubleshooting for {target_id}:")
                    if 'timeout' in reason.lower():
                        logging.info("       - Health check timing out - check if app responds on port 8006")
                        logging.info("       - Verify application binds to 0.0.0.0:8006 (not 127.0.0.1)")
                    elif 'connection refused' in reason.lower():
                        logging.info("       - Connection refused - check if service is running on port 8006")
                        logging.info("       - Verify security group allows traffic on port 8006")
                    elif 'target not found' in reason.lower():
                        logging.info("       - Target not found - check ECS task networking")
                        logging.info("       - Verify ENI and private IP assignment")
                    else:
                        logging.info(f"       - HTTP health check failed - check /health/ endpoint")
                        logging.info(f"       - Verify endpoint returns 200-399 status codes")
                elif state.lower() == 'initial':
                    logging.info("     ℹ️  Target is in initial state (normal for new deployments)")
            
            healthy_count = sum(1 for t in targets if t['TargetHealth']['State'] == 'healthy')
            logging.info(f"📊 Health Summary: {healthy_count}/{len(targets)} targets healthy")
            
            if healthy_count == 0:
                logging.info("⚠️  No healthy targets detected - investigating common issues:")
                logging.info("   1. Check ECS task logs for startup errors")
                logging.info("   2. Verify /health/ endpoint works: curl http://<task-ip>:8006/health/")
                logging.info("   3. Check security group ingress rules for port 8006")
                logging.info("   4. Verify application binds to 0.0.0.0:8006")
        
        except Exception as e:
            logging.info(f"❌ Error diagnosing target group health: {e}")
            raise
    
    def _find_sgr_import_id(self, cidr: str):
        """
        Shared helper to locate an existing security group rule for the given CIDR
        and return the correct OpenTofu composite import ID.

        If no matching rule exists, return None.
        """
        shared_vpc = self.get_shared_vpc()
        sg_id = shared_vpc.rds_security_group_id
        
        ec2 = self.client("ec2")
        response = ec2.describe_security_group_rules(
            Filters=[{"Name": "group-id", "Values": [sg_id]}]
        )
        
        for rule in response.get("SecurityGroupRules", []):
            if (
                    rule.get("IpProtocol") == "tcp"
                    and rule.get("FromPort") == 5432
                    and rule.get("ToPort") == 5432
                    and rule.get("CidrIpv4") == cidr
            ):
                return f"{sg_id}_ingress_tcp_5432_5432_{cidr}"
        
        return None
    
    def get_rds_ingress_vpc_rule_id(self):
        return self._find_sgr_import_id(self.get_shared_vpc().cidr_block)
    
    def get_rds_ingress_securitygroup_rule_id(self):
        return self._find_sgr_import_id(common.get_ip_address())
