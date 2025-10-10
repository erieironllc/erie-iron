import base64
import datetime
import ipaddress
import json
import logging
import os
import re
import threading
import time
import urllib
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Tuple
from urllib.parse import quote_plus
from urllib.parse import unquote_plus

import boto3
import rsa
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

import settings
from erieiron_common import common
from erieiron_common import settings_common
from erieiron_common.aws_s3_local_cache import S3LocalCache
from erieiron_common.date_utils import to_utc

logging.getLogger('botocore.credentials').setLevel(logging.ERROR)

STACK_STATUS_NO_STACK = "NO_STACK"
SHARED_VPC_NAME = "erie-iron-shared-vpc"
SHARED_VPC_CIDR = "10.90.0.0/16"
SHARED_VPC_ID = "vpc-0069eeaf60f597540"
SHARED_PUBLIC_SUBNETS = [
    ("erie-iron-shared-public-a", "10.90.0.0/20"),
    ("erie-iron-shared-public-b", "10.90.16.0/20"),
]
SHARED_PRIVATE_SUBNETS = [
    ("erie-iron-shared-private-a", "10.90.32.0/20"),
    ("erie-iron-shared-private-b", "10.90.48.0/20"),
]
SHARED_NAT_EIP_NAME = "erie-iron-shared-nat-eip"
SHARED_NAT_GATEWAY_NAME = "erie-iron-shared-nat-gateway"
SHARED_PUBLIC_ROUTE_TABLE_NAME = "erie-iron-shared-public-rt"
SHARED_PRIVATE_ROUTE_TABLE_NAME = "erie-iron-shared-private-rt"
SHARED_RDS_SECURITY_GROUP_NAME = "erie-iron-shared-rds-sg"
SHARED_RDS_SECURITY_GROUP_ID = "sg-05578f857876a5108"


@dataclass(slots=True)
class SharedVpcContext:
    vpc_id: str
    cidr_block: str
    public_subnet_ids: list[str]
    private_subnet_ids: list[str]


def client(client_name) -> boto3.session.Session.client:
    return boto3.client(client_name, region_name=get_aws_region())


def get_aws_region() -> str:
    return os.getenv("AWS_REGION", "us-west-2")


def ensure_network_access(
        db_host: str,
        aws_region=settings.AWS_DEFAULT_REGION_NAME
):
    rds = client("rds")
    ec2 = client("ec2")
    
    my_cidr = common.get_ip_address()
    db_instance, db_cluster = get_db_instance_and_cluster(db_host, aws_region)
    sg_ids = get_securitygroup_ids(db_cluster, db_instance)
    if not sg_ids:
        raise Exception(f"unable to fetch security group ids for {db_host} in region {settings.AWS_DEFAULT_REGION_NAME} ")
    
    for sg_id in sg_ids:
        sg_desc = ec2.describe_security_groups(GroupIds=[sg_id])["SecurityGroups"][0]
        
        has_rule = False
        for perm in sg_desc.get("IpPermissions", []):
            if perm.get("IpProtocol") == "tcp" and perm.get("FromPort") == 5432 and perm.get("ToPort") == 5432:
                if any(r.get("CidrIp") == my_cidr for r in perm.get("IpRanges", [])):
                    has_rule = True
                    break
        
        if has_rule:
            continue
        
        try:
            ec2.authorize_security_group_ingress(
                GroupId=sg_id,
                IpPermissions=[{
                    "IpProtocol": "tcp",
                    "FromPort": 5432,
                    "ToPort": 5432,
                    "IpRanges": [{"CidrIp": my_cidr, "Description": "jj laptop postgres access"}],
                }]
            )
        except ClientError as ce:
            if ce.response.get("Error", {}).get("Code") != "InvalidPermission.Duplicate":
                raise


def get_db_instance_and_cluster(
        db_host: str,
        aws_region: str
) -> tuple:
    db_instance = None
    db_cluster = None
    
    rds = client("rds")
    paginator = rds.get_paginator("describe_db_instances")
    for page in paginator.paginate():
        for inst in page.get("DBInstances", []):
            ep = (inst.get("Endpoint") or {}).get("Address")
            if ep and ep == db_host:
                db_instance = inst
    
    paginator = rds.get_paginator("describe_db_clusters")
    for page in paginator.paginate():
        for cl in page.get("DBClusters", []):
            endpoints = set(filter(None, [
                cl.get("Endpoint"),
                cl.get("ReaderEndpoint"),
                cl.get("CustomEndpoints", []),
            ] if isinstance(cl.get("CustomEndpoints"), list) else [cl.get("Endpoint"), cl.get("ReaderEndpoint")]))
            if db_host in endpoints:
                db_cluster = cl
    
    return db_instance, db_cluster


def get_securitygroup_ids(db_cluster, db_instance):
    sg_ids = []
    if db_instance:
        sg_ids = [v["VpcSecurityGroupId"] for v in db_instance.get("VpcSecurityGroups", []) if v.get("VpcSecurityGroupId")]
    elif db_cluster:
        sg_ids = [v["VpcSecurityGroupId"] for v in db_cluster.get("VpcSecurityGroups", []) if v.get("VpcSecurityGroupId")]
    return sg_ids


def ensure_iam_role_exists_and_get_arn(role_name: str) -> str:
    iam = boto3.client("iam")
    
    try:
        role = iam.get_role(RoleName=role_name)["Role"]
        return role["Arn"]
    except iam.exceptions.NoSuchEntityException:
        pass
    
    role = iam.create_role(**{
        "RoleName": role_name,
        "Description": f"Erie Iron role for {role_name}",
        "AssumeRolePolicyDocument": json.dumps({
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                },
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }
            ]
        }, indent=4)
    })["Role"]
    
    iam.attach_role_policy(
        RoleName=role_name,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
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
                    default=None
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
    sts = boto3.client('sts')
    account_id = sts.get_caller_identity()["Account"]
    
    org = boto3.client('organizations')
    resp = org.describe_account(AccountId=account_id)
    account_name = resp["Account"]["Name"]
    if account_name != required_account_name:
        raise Exception(f"Invalid Account {account_name} != {required_account_name}")


def get_asg_size(auto_scaling_group) -> Tuple[int, int, int]:
    response = client("autoscaling").describe_auto_scaling_groups(
        AutoScalingGroupNames=[auto_scaling_group.value]
    )
    
    if response["AutoScalingGroups"]:
        g = response["AutoScalingGroups"][0]
        return int(g["DesiredCapacity"]), int(g["MinSize"]), int(g["MaxSize"])
    else:
        raise ValueError(f"No Auto Scaling group not found for {auto_scaling_group.value}")


def set_asg_desired_capacity(auto_scaling_group, count: int):
    from erieiron_common import common
    current_size, min_intances, max_instances = get_asg_size(auto_scaling_group)
    count = max(min_intances, count)
    count = min(max_instances, count)
    
    if count == current_size:
        return
    
    common.log_info(f"UPDATING ASG Capacity {auto_scaling_group.value} to {count}")
    response = client('autoscaling').update_auto_scaling_group(
        AutoScalingGroupName=auto_scaling_group.value,
        DesiredCapacity=count
    )


def set_ecs_service_tasks(cluster_name, service_name, desired_count):
    client('ecs').update_service(
        cluster=cluster_name,
        service=service_name,
        desiredCount=desired_count
    )


def get_cloudwatch_url(around_time: datetime.datetime):
    if around_time is None:
        return None
    
    logging_start_time = int((around_time - datetime.timedelta(seconds=.1)).timestamp() * 1000)
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
    if isinstance(secret_name, str) and secret_name.startswith("arn:aws:secretsmanager:"):
        return secret_name
    
    try:
        resp = client('secretsmanager').describe_secret(SecretId=secret_name)
        arn = resp.get('ARN')
        if not arn:
            raise RuntimeError(f"DescribeSecret returned no ARN for {secret_name}")
        return arn
    except ClientError as e:
        code = e.response.get('Error', {}).get('Code')
        if code == 'ResourceNotFoundException':
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
    
    secrets_manager = client('secretsmanager')
    
    secret_string = json.dumps(val)
    
    try:
        # Check if the secret exists
        try:
            secrets_manager.describe_secret(SecretId=secret_name)
            exists = True
        except ClientError as e:
            err = e.response.get('Error', {}).get('Code')
            if err in ("ResourceNotFoundException",):
                exists = False
            else:
                raise
        
        if exists:
            resp = secrets_manager.put_secret_value(
                SecretId=secret_name,
                SecretString=secret_string
            )
            logging.info(f"Updated secret value for {secret_name}")
        else:
            resp = secrets_manager.create_secret(
                Name=secret_name,
                SecretString=secret_string
            )
            logging.info(f"Created secret {secret_name}")
        
        # Normalize return
        arn = resp.get('ARN') if 'ARN' in resp else resp.get('ARN', None)
        version_id = resp.get('VersionId') if 'VersionId' in resp else resp.get('VersionId', None)
        
        return arn
    except ClientError as e:
        logging.exception(e)
        raise


def get_secret(secret_name: str):
    # Create a Secrets Manager client
    session = boto3.session.Session()
    client = session.client(
        service_name='secretsmanager',
        region_name=os.getenv("AWS_REGION", "us-west-2")
    )
    
    try:
        get_secret_value_response = client.get_secret_value(
            SecretId=secret_name
        )
    except ClientError as e:
        raise e
    else:
        # Decrypts secret using the associated KMS CMK
        # Depending on whether the secret is a string or binary, one of these fields will be populated
        if 'SecretString' in get_secret_value_response:
            secret = get_secret_value_response['SecretString']
            return json.loads(secret)
        else:
            decoded_binary_secret = base64.b64decode(get_secret_value_response['SecretBinary'])
            return json.loads(decoded_binary_secret)


@lru_cache
def get_cloudfron_url_signing_private_key():
    response = client("secretsmanager").get_secret_value(
        SecretId=settings_common.CLOUDFRONT_PRIVATE_KEY_SECRET_NAME
    )
    
    return rsa.PrivateKey.load_pkcs1(response["SecretString"].encode("utf-8"))


def generate_signed_cloudfront_url(cloudfront_domain, s3_key, expires_in_seconds=604800):
    url = f"https://{cloudfront_domain}/{quote_plus(s3_key)}"
    expires = int(time.time()) + expires_in_seconds
    
    policy = f'''{{"Statement":[{{"Resource":"{url}","Condition":{{"DateLessThan":{{"AWS:EpochTime":{expires}}}}}}}]}}'''
    
    private_key = get_cloudfron_url_signing_private_key()
    signature = rsa.sign(policy.encode(), private_key, 'SHA-1')
    encoded_sig = quote_plus(base64.b64encode(signature).decode("utf-8"))
    
    url = f"{url}?Expires={expires}&Signature={encoded_sig}&Key-Pair-Id={settings_common.CLOUDFRONT_KEY_PAIR_ID}"
    
    return url


def get_objpk_from_s3key(s3_key: str):
    return s3_key.split(".")[0]


def get_filename(event):
    from erieiron_common import common
    record = event['Records'][0]
    bucket = common.get(record, ["s3", 'bucket', 'name']).strip()
    input_file_key = unquote_plus(common.get(record, ["s3", 'object', 'key'])).strip()
    return bucket, input_file_key


def set_aws_interface(aws_interface):
    from erieiron_common import cache
    aws_interface = cache.tl_set("aws_interface", aws_interface)


def get_aws_interface():
    from erieiron_common import cache
    aws_interface = cache.tl_get("aws_interface")
    if aws_interface is None:
        aws_interface = AwsInterface()
        set_aws_interface(aws_interface)
    return aws_interface


class AwsInterface:
    def __init__(self):
        self.s3_passthrough_cache = S3LocalCache(client('s3'))
    
    def generate_presigned_url(self, bucket: str, file_key: str, content_type: str, expiration_minutes=1000):
        return boto3.client("s3").generate_presigned_url(
            'put_object',
            Params={
                'Bucket': bucket,
                'Key': file_key,
                'ContentType': content_type,
            },
            ExpiresIn=expiration_minutes * 60
        )
    
    def assert_test_interface(self):
        raise Exception("Expecting test interface, but got real one")
    
    def transcribe_audio(self, bucket, key):
        transcribe = client('transcribe')
        
        job_name = f"{key}_{uuid.uuid4()}"
        
        response = transcribe.start_transcription_job(
            TranscriptionJobName=job_name,
            Media={'MediaFileUri': f"s3://{bucket}/{key}"},
            MediaFormat=key.split(".")[-1],
            LanguageCode='en-US'
        )
        
        while True:
            status = transcribe.get_transcription_job(TranscriptionJobName=job_name)
            if status['TranscriptionJob']['TranscriptionJobStatus'] in ['COMPLETED', 'FAILED']:
                break
            else:
                time.sleep(2)
        
        if status['TranscriptionJob']['TranscriptionJobStatus'] == 'COMPLETED':
            result = transcribe.get_transcription_job(TranscriptionJobName=job_name)
            result_uri = result['TranscriptionJob']['Transcript']['TranscriptFileUri']
            
            # noinspection PyUnresolvedReferences
            with urllib.request.urlopen(result_uri) as response:
                raw_json = response.read()
            transcription_result = json.loads(raw_json)
            
            from erieiron_common import common
            
            results = transcription_result['results']
            
            return results
        else:
            return None
    
    def transcribe_audio_openai(self, bucket, key):
        import openai
        # Download the audio file from S3 using the existing download method
        file_path = self.download(bucket, key)
        
        # Open the downloaded file and transcribe using OpenAI's Whisper API
        with open(file_path, 'rb') as audio_file:
            transcript = openai.Audio.transcribe('whisper-1', audio_file)
        
        return transcript
    
    def get_log_events(self, log_group, start_date: datetime.date, end_date: datetime.date = None, filter_pattern=''):
        from erieiron_common import common
        
        start_time, _ = common.date_to_epoch_ms(start_date)
        kwargs = {
            'logGroupName': log_group,
            'filterPattern': filter_pattern,
            'startTime': start_time
        }
        
        if end_date is not None:
            _, end_time = common.date_to_epoch_ms(end_date)
            kwargs['endTime'] = end_time
        
        page_iterator = boto3.client(
            'logs',
            region_name=settings_common.AWS_DEFAULT_REGION_NAME
        ).get_paginator(
            'filter_log_events'
        ).paginate(**kwargs)
        
        for page in page_iterator:
            for event in page['events']:
                yield event['message']
    
    def send_email_from_template(self, subject, sender, recipient, template_name, ctx):
        from erieiron_common import common
        body = common.render_template(template_name, ctx)
        
        self.send_email(
            subject=subject,
            sender=sender,
            recipient=recipient,
            body=body
        )
    
    def send_email(self, subject, recipient, body, sender="erieironllc@gmail.com"):
        from erieiron_common import common
        common.log_info(f"sending email: {subject} to {recipient}")
        
        sender = common.default_str(sender, settings_common.FEEDBACK_EMAIL)
        ses_client = boto3.client('ses', region_name=settings_common.AWS_DEFAULT_REGION_NAME)
        
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
        
        if settings_common.DISABLE_EMAIL_SEND:
            logging.info(f"""
NOT SENDING THIS EMAIL (settings.DISABLE_EMAIL_SEND=False)

TO:
{recipient}

FROM:
{sender}

SUBJECT:
{subject}

BODY:
{body}

            """)
            return
        
        ses_client.send_email(
            Source=sender,
            Destination={
                'ToAddresses': [
                    recipient,
                ]
            },
            Message={
                'Subject': {
                    'Data': subject,
                    'Charset': 'UTF-8'
                },
                'Body': {
                    'Text': {
                        'Data': text_body,
                        'Charset': 'UTF-8'
                    },
                    'Html': {
                        'Data': html_body,
                        'Charset': 'UTF-8'
                    }
                }
            }
        )
    
    def object_exists(self, bucket: str, key: str):
        try:
            self.assert_object_exists(bucket, key)
            return True
        except:
            return False
    
    def assert_object_exists(self, bucket: str, key: str):
        boto3.client("s3").head_object(
            Bucket=bucket,
            Key=key
        )
    
    def create_empty_file(self, bucket: str, key: str) -> str:
        boto3.client("s3").put_object(Bucket=bucket, Key=key)
    
    def delete(self, bucket: str, key: str) -> str:
        boto3.client("s3").delete_object(Bucket=bucket, Key=key)
    
    def download(self, bucket: str, key: str) -> str:
        return str(self.s3_passthrough_cache.get_file(bucket, key))
    
    def download_all(self, dest_dir: Path, bucket_name, prefix):
        dest_dir = Path(dest_dir).expanduser()
        dest_dir.mkdir(parents=True, exist_ok=True)
        s3 = boto3.client('s3')
        
        paginator = s3.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
            for obj in page.get('Contents', []):
                key = obj['Key']
                # skip directories
                if key.endswith('/'):
                    continue
                
                s3.download_file(
                    bucket_name,
                    key,
                    dest_dir / key.split('/')[-1]
                )
    
    def send_client_message(self, person, message_type, payload):
        from erieiron_common.json_encoder import ErieIronJSONEncoder
        from erieiron_common import common
        
        if not person:
            raise ValueError("missing person")
        if not message_type:
            raise ValueError("missing message type")
        payload = payload or {}
        
        apigateway_client = boto3.client(
            'apigatewaymanagementapi',
            region_name=settings_common.AWS_DEFAULT_REGION_NAME,
            endpoint_url=f"https://{settings_common.CLIENT_MESSAGE_WEBSOCKET_ENDPOINT}"
        )
        
        table = boto3.resource(
            'dynamodb',
            region_name=settings_common.AWS_DEFAULT_REGION_NAME
        ).Table(
            settings_common.CLIENT_MESSAGE_DYNAMO_TABLE
        )
        
        # query for all connections open for this person
        response = table.query(
            IndexName="person_id-index",
            KeyConditionExpression=boto3.dynamodb.conditions.Key("person_id").eq(str(person.pk))
        )
        
        # send message to all connections
        for item in response.get('Items', []):
            connection_id = item['connection_id']
            try:
                apigateway_client.post_to_connection(
                    ConnectionId=connection_id,
                    Data=json.dumps({
                        "message_type": message_type,
                        "payload": payload
                    }, cls=ErieIronJSONEncoder)
                )
                common.log_debug(f"Websocket {message_type} message sent to connection: {connection_id}")
            except ClientError as e:
                if e.response['Error']['Code'] == 'GoneException':
                    common.log_debug(f"Websocket connection {connection_id} no longer exists.")
                    
                    try:
                        table.delete_item(
                            Key={
                                'connection_id': connection_id
                            }
                        )
                    except Exception as e2:
                        common.log_error(f"failed to delete connection {connection_id} {e2}")
                else:
                    # Handle other potential exceptions
                    raise e
    
    def delete_websocket_connection(self, connection_id):
        from erieiron_common import common
        table = boto3.resource('dynamodb').Table("client-messages-db")
        
        response = table.query(
            IndexName="connection_id-index",
            KeyConditionExpression=boto3.dynamodb.conditions.Key("connection_id").eq(connection_id)
        )
        
        response = table.delete_item(
            Key={
                'connection_id': connection_id  # Primary key field and value
            }
        )
        
        items = response.get("Items", [])
        
        common.log_info(f"Found {len(items)} items to delete for connection_id={connection_id}")
        
        for item in items:
            table.delete_item(
                Key={
                    "person_id": item["person_id"],
                    "connection_id": connection_id
                }
            )
            common.log_info(f'Deleted item with primary key: {item["person_id"], item["connection_id"]}')
        
        common.log_info("Deletion complete.")
    
    def iterate_s3_bucket_keys(self, bucket_name):
        s3 = boto3.client("s3")
        
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
            delete_when_done: bool = False
    ):
        if not os.path.exists(file_path):
            raise Exception(f"{file_path} does not exist")
        
        from erieiron_common import common
        name = common.get(file_path, "name", str(file_path))
        common.log_debug(f"uploading {name} to s3://{dest_bucket}/{dest_key}")
        
        if asyncronous:
            threading.Thread(
                target=self._upload_file,
                args=(
                    file_path,
                    dest_bucket,
                    dest_key,
                    content_type,
                    delete_when_done
                )
            ).start()
        else:
            self._upload_file(
                file_path,
                dest_bucket,
                dest_key,
                content_type,
                delete_when_done
            )
            common.log_debug(f"uploaded {name} to s3://{dest_bucket}/{dest_key}")
    
    def _upload_file(
            self,
            file_path: str,
            dest_bucket: str,
            dest_key: str,
            content_type: str,
            delete_when_done: bool
    ):
        self.s3_passthrough_cache.put_file(
            file_path=file_path,
            bucket_name=dest_bucket,
            key=dest_key,
            content_type=content_type
        )
        
        # if delete_when_done:
        #     from erieiron_common.common import quietly_delete
        #     quietly_delete(file_path)


def _describe_availability_zones(region: str) -> list[str]:
    ec2_client = boto3.client("ec2", region_name=region)
    zones = []
    resp = ec2_client.describe_availability_zones(AllAvailabilityZones=False)
    for az in resp.get("AvailabilityZones", []):
        if az.get("State") == "available":
            zones.append(az.get("ZoneName"))
    return sorted(filter(None, zones)) or [f"{region}a"]


def ensure_shared_vpc_exists(aws_env) -> SharedVpcContext:
    region = aws_env.get_aws_region()
    ec2_resource = boto3.resource("ec2", region_name=region)
    ec2_client = boto3.client("ec2", region_name=region)
    
    vpcs = list(
        ec2_resource.vpcs.filter(
            Filters=[{"Name": "tag:Name", "Values": [SHARED_VPC_NAME]}]
        )
    )
    
    if vpcs:
        vpc = vpcs[0]
    else:
        logging.info("Creating shared VPC %s in %s", SHARED_VPC_NAME, region)
        vpc = ec2_resource.create_vpc(CidrBlock=SHARED_VPC_CIDR)
        vpc.wait_until_available()
        vpc.create_tags(
            Tags=[
                {"Key": "Name", "Value": SHARED_VPC_NAME},
                {"Key": "ManagedBy", "Value": "ErieIron"},
            ]
        )
        ec2_client.modify_vpc_attribute(
            VpcId=vpc.id,
            EnableDnsHostnames={"Value": True}
        )
        ec2_client.modify_vpc_attribute(
            VpcId=vpc.id,
            EnableDnsSupport={"Value": True}
        )
    
    azs = _describe_availability_zones(region)
    
    def ensure_subnet(name: str, cidr: str, az_index: int, map_public_ip: bool) -> str:
        existing = list(
            ec2_resource.subnets.filter(
                Filters=[{"Name": "tag:Name", "Values": [name]}]
            )
        )
        if existing:
            subnet = existing[0]
        else:
            # Check for CIDR conflicts in the VPC before creating
            try:
                subnets_resp = ec2_client.describe_subnets(
                    Filters=[
                        {"Name": "vpc-id", "Values": [vpc.id]}
                    ]
                )
            except Exception as exc:
                logging.warning("Failed to describe subnets for VPC %s: %s", vpc.id, exc)
                subnets_resp = {"Subnets": []}
            requested_cidr = ipaddress.ip_network(cidr)
            conflict_subnet_id = None
            for s in subnets_resp.get("Subnets", []):
                s_cidr = s.get("CidrBlock")
                s_id = s.get("SubnetId")
                if not s_cidr or not s_id:
                    continue
                try:
                    s_net = ipaddress.ip_network(s_cidr)
                except Exception:
                    continue
                # If there is any overlap
                if requested_cidr.overlaps(s_net):
                    conflict_subnet_id = s_id
                    break
            if conflict_subnet_id:
                logging.warning("CIDR conflict detected for %s (%s); using existing subnet %s", name, cidr, conflict_subnet_id)
                return conflict_subnet_id
            az = azs[az_index % len(azs)]
            logging.info("Creating subnet %s (%s) in %s", name, cidr, az)
            subnet = ec2_resource.create_subnet(
                VpcId=vpc.id,
                CidrBlock=cidr,
                AvailabilityZone=az
            )
            # Wait for subnet to become available (no built-in waiter in boto3)
            for _ in range(10):
                desc = ec2_client.describe_subnets(SubnetIds=[subnet.id])["Subnets"][0]
                if desc.get("State") == "available":
                    break
                time.sleep(2)
            subnet.create_tags(
                Tags=[
                    {"Key": "Name", "Value": name},
                    {"Key": "ManagedBy", "Value": "ErieIron"},
                ]
            )
        try:
            ec2_client.modify_subnet_attribute(
                SubnetId=subnet.id,
                MapPublicIpOnLaunch={"Value": map_public_ip}
            )
        except ClientError as exc:
            logging.warning("Failed to update MapPublicIpOnLaunch for %s: %s", subnet.id, exc)
        return subnet.id
    
    public_subnets = [
        ensure_subnet(name, cidr, idx, True)
        for idx, (name, cidr) in enumerate(SHARED_PUBLIC_SUBNETS)
    ]
    private_subnets = [
        ensure_subnet(name, cidr, idx, False)
        for idx, (name, cidr) in enumerate(SHARED_PRIVATE_SUBNETS)
    ]
    
    # Ensure internet gateway for the shared VPC
    igws = list(
        ec2_resource.internet_gateways.filter(
            Filters=[{"Name": "attachment.vpc-id", "Values": [vpc.id]}]
        )
    )
    if igws:
        igw = igws[0]
    else:
        igw = ec2_resource.create_internet_gateway()
        igw.create_tags(
            Tags=[
                {"Key": "Name", "Value": f"{SHARED_VPC_NAME}-igw"},
                {"Key": "ManagedBy", "Value": "ErieIron"},
            ]
        )
        igw.attach_to_vpc(VpcId=vpc.id)
    
    # Public route table with default route to IGW
    def ensure_route_table(name: str):
        tables = list(
            ec2_resource.route_tables.filter(
                Filters=[{"Name": "tag:Name", "Values": [name]}]
            )
        )
        if tables:
            table = tables[0]
        else:
            table = ec2_resource.create_route_table(VpcId=vpc.id)
            table.create_tags(
                Tags=[
                    {"Key": "Name", "Value": name},
                    {"Key": "ManagedBy", "Value": "ErieIron"},
                ]
            )
        return table
    
    public_route_table = ensure_route_table(SHARED_PUBLIC_ROUTE_TABLE_NAME)
    try:
        ec2_client.create_route(
            RouteTableId=public_route_table.id,
            DestinationCidrBlock="0.0.0.0/0",
            GatewayId=igw.id
        )
    except ClientError as exc:
        if "RouteAlreadyExists" not in str(exc):
            logging.warning("Failed to create default route on %s: %s", public_route_table.id, exc)
    
    for subnet_id in public_subnets:
        associations = public_route_table.associations
        if not any(getattr(assoc, "subnet_id", None) == subnet_id for assoc in associations):
            try:
                public_route_table.associate_with_subnet(SubnetId=subnet_id)
            except ClientError as exc:
                logging.warning("Failed to associate %s with %s: %s", subnet_id, public_route_table.id, exc)
    
    def ensure_nat_gateway(subnet_id: str) -> str:
        nat_resp = ec2_client.describe_nat_gateways(
            Filters=[
                {"Name": "tag:Name", "Values": [SHARED_NAT_GATEWAY_NAME]},
                {"Name": "state", "Values": ["available", "pending"]},
            ]
        )
        nat_gateways = nat_resp.get("NatGateways", [])
        if nat_gateways:
            nat = nat_gateways[0]
            nat_gateway_id = nat.get("NatGatewayId")
            if nat.get("State") != "available" and nat_gateway_id:
                waiter = ec2_client.get_waiter("nat_gateway_available")
                try:
                    waiter.wait(NatGatewayIds=[nat_gateway_id])
                except Exception as exc:
                    logging.warning("Waiter for NAT gateway %s failed: %s", nat_gateway_id, exc)
            return nat_gateway_id
        
        eip_resp = ec2_client.describe_addresses(
            Filters=[{"Name": "tag:Name", "Values": [SHARED_NAT_EIP_NAME]}]
        )
        eip_addresses = eip_resp.get("Addresses", [])
        allocation_id = None
        for address in eip_addresses:
            if address.get("Domain") == "vpc" and address.get("AllocationId"):
                allocation_id = address["AllocationId"]
                break
        if not allocation_id:
            alloc_resp = ec2_client.allocate_address(
                Domain="vpc",
                TagSpecifications=[
                    {
                        "ResourceType": "elastic-ip",
                        "Tags": [
                            {"Key": "Name", "Value": SHARED_NAT_EIP_NAME},
                            {"Key": "ManagedBy", "Value": "ErieIron"},
                        ],
                    }
                ],
            )
            allocation_id = alloc_resp.get("AllocationId")
        
        create_resp = ec2_client.create_nat_gateway(
            SubnetId=subnet_id,
            AllocationId=allocation_id,
            TagSpecifications=[
                {
                    "ResourceType": "natgateway",
                    "Tags": [
                        {"Key": "Name", "Value": SHARED_NAT_GATEWAY_NAME},
                        {"Key": "ManagedBy", "Value": "ErieIron"},
                    ],
                }
            ],
        )
        nat_gateway_id = (create_resp.get("NatGateway") or {}).get("NatGatewayId")
        if nat_gateway_id:
            waiter = ec2_client.get_waiter("nat_gateway_available")
            try:
                waiter.wait(NatGatewayIds=[nat_gateway_id])
            except Exception as exc:
                logging.warning("Waiter for NAT gateway %s failed: %s", nat_gateway_id, exc)
        return nat_gateway_id
    
    nat_gateway_id = ensure_nat_gateway(public_subnets[0])
    
    private_route_table = ensure_route_table(SHARED_PRIVATE_ROUTE_TABLE_NAME)
    if nat_gateway_id:
        try:
            ec2_client.create_route(
                RouteTableId=private_route_table.id,
                DestinationCidrBlock="0.0.0.0/0",
                NatGatewayId=nat_gateway_id
            )
        except ClientError as exc:
            if "RouteAlreadyExists" not in str(exc):
                logging.warning("Failed to create private route on %s: %s", private_route_table.id, exc)
    
    for subnet_id in private_subnets:
        associations = private_route_table.associations
        if not any(getattr(assoc, "subnet_id", None) == subnet_id for assoc in associations):
            try:
                private_route_table.associate_with_subnet(SubnetId=subnet_id)
            except ClientError as exc:
                logging.warning("Failed to associate %s with %s: %s", subnet_id, private_route_table.id, exc)
    
    return SharedVpcContext(
        vpc_id=vpc.id,
        cidr_block=getattr(vpc, "cidr_block", SHARED_VPC_CIDR),
        public_subnet_ids=public_subnets,
        private_subnet_ids=private_subnets,
    )


def ensure_shared_rds_security_group(
        aws_env=None,
        developer_cidr: str | None = None
) -> str:
    shared_vpc = get_shared_vpc()
    
    if aws_env is not None:
        region = aws_env.get_aws_region()
    else:
        region = get_aws_region()
    ec2_client = boto3.client("ec2", region_name=region)
    ec2_resource = boto3.resource("ec2", region_name=region)
    
    filters = [
        {"Name": "tag:Name", "Values": [SHARED_RDS_SECURITY_GROUP_NAME]},
        {"Name": "vpc-id", "Values": [shared_vpc.vpc_id]}
    ]
    resp = ec2_client.describe_security_groups(Filters=filters)
    groups = resp.get("SecurityGroups", [])
    
    if groups:
        sg_id = groups[0]["GroupId"]
        sg = ec2_resource.SecurityGroup(sg_id)
    else:
        logging.info("Creating shared RDS security group %s", SHARED_RDS_SECURITY_GROUP_NAME)
        create_resp = ec2_client.create_security_group(
            GroupName=SHARED_RDS_SECURITY_GROUP_NAME,
            Description="Shared database access for Erie Iron stacks",
            VpcId=shared_vpc.vpc_id
        )
        sg_id = create_resp.get("GroupId")
        ec2_client.create_tags(
            Resources=[sg_id],
            Tags=[
                {"Key": "Name", "Value": SHARED_RDS_SECURITY_GROUP_NAME},
                {"Key": "ManagedBy", "Value": "ErieIron"},
            ]
        )
        sg = ec2_resource.SecurityGroup(sg_id)
    
    desired_cidrs = [shared_vpc.cidr_block]
    if developer_cidr:
        desired_cidrs.append(developer_cidr)
    
    def _has_rule(cidr: str) -> bool:
        for perm in sg.ip_permissions:
            if perm.get("IpProtocol") != "tcp":
                continue
            if perm.get("FromPort") != 5432 or perm.get("ToPort") != 5432:
                continue
            for ip_range in perm.get("IpRanges", []):
                if ip_range.get("CidrIp") == cidr:
                    return True
        return False
    
    for cidr in filter(None, desired_cidrs):
        if _has_rule(cidr):
            continue
        try:
            sg.authorize_ingress(
                IpPermissions=[
                    {
                        "IpProtocol": "tcp",
                        "FromPort": 5432,
                        "ToPort": 5432,
                        "IpRanges": [
                            {
                                "CidrIp": cidr,
                                "Description": "Erie Iron shared database access",
                            }
                        ],
                    }
                ]
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "InvalidPermission.Duplicate":
                logging.warning("Failed adding SG ingress for %s: %s", cidr, exc)
    
    return sg.id


@lru_cache(maxsize=1)
def get_shared_vpc() -> SharedVpcContext:
    region = get_aws_region()
    ec2_client = boto3.client("ec2", region_name=region)
    
    try:
        vpc_resp = ec2_client.describe_vpcs(VpcIds=[SHARED_VPC_ID])
    except ClientError as exc:
        raise RuntimeError(
            f"Failed to describe shared VPC {SHARED_VPC_ID} in {region}: {exc}"
        ) from exc
    
    vpcs = vpc_resp.get("Vpcs", [])
    if not vpcs:
        raise RuntimeError(f"Shared VPC {SHARED_VPC_ID} not found in {region}")
    
    vpc = vpcs[0]
    cidr_block = vpc.get("CidrBlock") or SHARED_VPC_CIDR
    
    def _resolve_subnets(subnet_specs):
        if not subnet_specs:
            return []
        
        expected_names = [name for name, _ in subnet_specs]
        expected_cidrs = [cidr for _, cidr in subnet_specs]
        
        try:
            subnet_resp = ec2_client.describe_subnets(
                Filters=[
                    {"Name": "vpc-id", "Values": [SHARED_VPC_ID]},
                    {"Name": "tag:Name", "Values": expected_names},
                ]
            )
        except ClientError as exc:
            raise RuntimeError(
                f"Failed describing shared subnets in {SHARED_VPC_ID}: {exc}"
            ) from exc
        
        name_to_id = {}
        vpc_ids_for_names = {}
        cidr_to_id = {}
        for subnet in subnet_resp.get("Subnets", []):
            tags = {tag.get("Key"): tag.get("Value") for tag in subnet.get("Tags", []) if tag.get("Key")}
            subnet_name = tags.get("Name")
            subnet_id = subnet.get("SubnetId")
            vpc_id = subnet.get("VpcId")
            cidr_block = subnet.get("CidrBlock")
            if subnet_name and subnet_id:
                name_to_id[subnet_name] = subnet_id
                vpc_ids_for_names[subnet_name] = vpc_id
            if cidr_block and subnet_id:
                cidr_to_id[cidr_block] = subnet_id
        
        missing = [name for name in expected_names if name not in name_to_id]
        # If any subnets missing, try to find them anywhere (not just in SHARED_VPC_ID)
        if missing:
            try:
                fallback_resp = ec2_client.describe_subnets(
                    Filters=[
                        {"Name": "tag:Name", "Values": missing}
                    ]
                )
            except ClientError as exc:
                raise RuntimeError(
                    f"Failed fallback search for shared subnets {missing}: {exc}"
                ) from exc
            found_any = False
            for subnet in fallback_resp.get("Subnets", []):
                tags = {tag.get("Key"): tag.get("Value") for tag in subnet.get("Tags", []) if tag.get("Key")}
                subnet_name = tags.get("Name")
                subnet_id = subnet.get("SubnetId")
                vpc_id = subnet.get("VpcId")
                cidr_block = subnet.get("CidrBlock")
                if subnet_name and subnet_id:
                    if subnet_name in missing:
                        found_any = True
                        # Warn if found in another VPC
                        if vpc_id != SHARED_VPC_ID:
                            logging.warning(
                                "Found shared subnet %s in VPC %s instead of %s; updating SHARED_VPC_ID",
                                subnet_name, vpc_id, SHARED_VPC_ID
                            )
                        name_to_id[subnet_name] = subnet_id
                        vpc_ids_for_names[subnet_name] = vpc_id
                if cidr_block and subnet_id:
                    cidr_to_id[cidr_block] = subnet_id
            still_missing = [name for name in expected_names if name not in name_to_id]
            # If still missing, try CIDR-based lookup as a fallback
            if still_missing:
                try:
                    cidr_fallback_resp = ec2_client.describe_subnets(
                        Filters=[
                            {"Name": "cidr-block", "Values": [cidr for _, cidr in subnet_specs]}
                        ]
                    )
                except ClientError as exc:
                    raise RuntimeError(
                        f"Failed CIDR fallback search for shared subnets {still_missing}: {exc}"
                    ) from exc
                for subnet in cidr_fallback_resp.get("Subnets", []):
                    subnet_id = subnet.get("SubnetId")
                    cidr_block = subnet.get("CidrBlock")
                    tags = {tag.get("Key"): tag.get("Value") for tag in subnet.get("Tags", []) if tag.get("Key")}
                    subnet_name = tags.get("Name")
                    # Try to map missing entries by CIDR
                    for idx, expected_name in enumerate(expected_names):
                        expected_cidr = subnet_specs[idx][1]
                        if expected_name not in name_to_id and cidr_block == expected_cidr:
                            name_to_id[expected_name] = subnet_id
                            logging.warning("Resolved missing subnet %s by CIDR match (%s -> %s)", expected_name, cidr_block, subnet_id)
                still_missing = [name for name in expected_names if name not in name_to_id]
                if still_missing:
                    raise RuntimeError(
                        f"Missing expected shared subnets {still_missing} in VPC {SHARED_VPC_ID} or any other VPC (including CIDR fallback)"
                    )
        
        return [name_to_id[name] for name in expected_names]
    
    public_subnet_ids = _resolve_subnets(SHARED_PUBLIC_SUBNETS)
    private_subnet_ids = _resolve_subnets(SHARED_PRIVATE_SUBNETS)
    
    return SharedVpcContext(
        vpc_id=vpc.get("VpcId", SHARED_VPC_ID),
        cidr_block=cidr_block,
        public_subnet_ids=public_subnet_ids,
        private_subnet_ids=private_subnet_ids,
    )


def read_cloudformation_failures(
        aws_region: str,
        stack_name: str,
        start_time: float,
        local_logs: str | None = None
) -> str:
    error_lines = []
    cf_client = boto3.client("cloudformation", region_name=aws_region)
    deployment_start_datetime = to_utc(start_time)

    def _collect_recent_events(
            target_stack: str,
            visited: set[str]
    ) -> tuple[list[dict], list[str]]:
        """Recursively gather stack events for the stack and any nested stacks."""
        local_errors: list[str] = []
        recent: list[dict] = []

        identifier = target_stack
        if identifier in visited:
            return recent, local_errors

        try:
            events_resp = cf_client.describe_stack_events(StackName=identifier)
        except Exception as exc:  # pragma: no cover - defensive guard for AWS API quirkiness
            local_errors.append(f"Failed to fetch stack events for {identifier}: {exc}")
            return recent, local_errors

        events = events_resp.get("StackEvents", [])

        # Mark the stack as visited using both the identifier we queried and any StackId we observe
        visited.add(identifier)
        for evt in events:
            stack_id = evt.get("StackId")
            if stack_id:
                visited.add(stack_id)

        for evt in events:
            timestamp = evt.get("Timestamp")
            if timestamp and timestamp >= deployment_start_datetime:
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
            nested_events, nested_errors = _collect_recent_events(nested_identifier, visited)
            recent.extend(nested_events)
            local_errors.extend(nested_errors)

        return recent, local_errors

    try:
        recent_events, collection_errors = _collect_recent_events(stack_name, set())
        recent_events.sort(key=lambda x: (x.get('Timestamp'), x.get('StackName', '')))  # type: ignore[arg-type]

        failed_events: list[str] = []
        failed_logical_ids: list[str] = []
        seen_failed_ids: set[str] = set()

        for event in recent_events:
            status = event.get("ResourceStatus")
            if not (isinstance(status, str) and "FAILED" in status):
                continue

            failed_events.append(
                f"{event['Timestamp']} | Stack: {event.get('StackName')} | {event.get('LogicalResourceId')} "
                f"({event.get('ResourceType')}) | Status: {status} | Reason: {event.get('ResourceStatusReason', '')} | "
                f"PhysicalId: {event.get('PhysicalResourceId', '')} | Token: {event.get('ClientRequestToken', '')}"
            )

            logical_id = event.get("LogicalResourceId")
            if logical_id and logical_id not in seen_failed_ids:
                failed_logical_ids.append(logical_id)
                seen_failed_ids.add(logical_id)

        error_lines.append("CloudFormation failure events:")
        if failed_events:
            error_lines.append("\n".join(failed_events))
        else:
            error_lines.append("No CloudFormation failure events found for the specified time window.")

        error_lines.append("\nDetailed resource descriptions for top failures:")
        for logical_id in failed_logical_ids[:3]:
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
                error_lines.append(json.dumps(resource_details, indent=2, default=str))
            except Exception as ex:  # pragma: no cover - AWS failures should not crash aggregation
                error_lines.append(f"Failed to describe resource {logical_id}: {ex}")

        if collection_errors:
            error_lines.append("\nIssues encountered while collecting stack events:")
            error_lines.extend(collection_errors)
    except Exception as event_ex:
        error_lines.append(f"Failed to fetch stack events: {event_ex}")

    error_lines.append("\nRecent CloudTrail events in this region (last 15 min):")
    try:
        ct_client = boto3.client("cloudtrail", region_name=aws_region)
        now = common.get_now()
        ct_query_start_time = now - datetime.timedelta(minutes=15)
        
        ct_events = ct_client.lookup_events(
            StartTime=ct_query_start_time,
            EndTime=now,
            MaxResults=100
        )
        
        deployment_start_datetime = to_utc(start_time)
        recent_ct_events = [
            event for event in ct_events.get("Events", [])
            if event['EventTime'] >= deployment_start_datetime
        ]
        recent_ct_events.sort(key=lambda x: x['EventTime'])
        
        for ct_event in recent_ct_events:
            cloudtrain_event_data = json.loads(ct_event["CloudTrailEvent"])
            error_message: str = cloudtrain_event_data.get("errorMessage")
            if error_message:
                if "No updates are to be performed" in error_message:
                    continue
                if error_message.lower().startswith("stack with id") and error_message.lower().endswith("does not exist"):
                    continue
                error_lines.append(f'\n\n\nCloudTrail Event: {ct_event.get("EventName")} at {ct_event.get("EventTime")}\n')
                error_lines.append(json.dumps(cloudtrain_event_data, indent=4))
    except Exception as ct_ex:
        error_lines.append(f"Failed to fetch CloudTrail events: {ct_ex}")
    
    effective_end_time = time.time()
    try:
        start_epoch = max(0, int(start_time) - 60)
        end_epoch = int(effective_end_time) + 60
        error_lines.append("\nCloudWatch logs for stack resources since deployment start:")
        stack_logs = extract_cloudwatch_stack_logs_for_window(
            stack_name=stack_name,
            start_time=start_epoch,
            end_time=end_epoch
        )
        if stack_logs:
            error_lines.append(stack_logs)
        else:
            error_lines.append("No CloudWatch log entries found for stack resources in this window.")
    except Exception as stack_log_ex:
        error_lines.append(f"Failed to collect CloudWatch logs for stack resources: {stack_log_ex}")
    
    if local_logs:
        try:
            lambda_logs = extract_cloudwatch_lambda_logs(
                local_logs=local_logs,
                lookback_minutes=120
            )
            if lambda_logs:
                error_lines.append("\nCloudWatch Lambda logs for RequestIds observed in local logs:")
                error_lines.append(lambda_logs)
        except Exception as lambda_ex:
            error_lines.append(f"Failed to collect CloudWatch Lambda logs: {lambda_ex}")
    
    return common.safe_join(error_lines, "\n")


# Helper to extract CloudWatch stack logs for a time window
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


# Helper to extract CloudWatch Lambda logs given RequestIds
def extract_cloudwatch_lambda_logs(
        local_logs: str,
        lookback_minutes: int = 60,
        max_groups: int = 50
) -> str:
    """
    Fetch CloudWatch Logs Insights for Lambda log groups that contain any of the given RequestIds.
    We scan recent logs (lookback window) across up to `max_groups` Lambda log groups.
    Returns a concatenated text block, sorted by timestamp, suitable for appending to error output.
    """
    request_ids = re.findall(r"RequestId:\s*([0-9a-fA-F\-]{36})", local_logs)
    if not request_ids:
        return ""
    
    logs = client("logs")
    
    # Discover Lambda log groups (limit to keep queries bounded)
    log_groups = []
    next_token = None
    try:
        while True:
            kwargs = {"logGroupNamePrefix": "/aws/lambda/"}
            if next_token:
                kwargs["nextToken"] = next_token
            resp = logs.describe_log_groups(**kwargs)
            for lg in resp.get("logGroups", []):
                name = lg.get("logGroupName")
                if name:
                    log_groups.append(name)
                    if len(log_groups) >= max_groups:
                        break
            if len(log_groups) >= max_groups or not resp.get("nextToken"):
                break
            next_token = resp.get("nextToken")
    except Exception as e:
        logging.info(f"Failed to list CloudWatch log groups: {e}")
        return ""
    
    if not log_groups:
        return ""
    
    end = int(time.time())
    start = end - lookback_minutes * 60
    
    # Build a Logs Insights query that matches any of the request IDs
    # We OR the patterns to reduce the number of queries.
    request_ids = [rid for rid in request_ids if isinstance(rid, str) and len(rid) >= 36]
    if not request_ids:
        return ""
    
    # Escape single quotes in IDs just in case
    or_terms = ["@message like '" + rid.replace("'", "\\'") + "'" for rid in request_ids]
    or_filters = " or ".join(or_terms)
    query_str = "fields @timestamp, @log, @message | filter " + or_filters + " | sort @timestamp asc | limit 1000"
    
    combined = []
    # Run the query against all discovered groups in small batches to stay within API limits
    batch_size = 10
    for i in range(0, len(log_groups), batch_size):
        batch = log_groups[i:i + batch_size]
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
        
        # Poll for completion
        status = "Running"
        for _ in range(60):
            time.sleep(1)
            resp = logs.get_query_results(queryId=query_id)
            status = resp.get("status")
            if status in ("Complete", "Failed", "Cancelled", "Timeout"):
                results = resp.get("results", [])
                # Flatten results into text lines
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
