import base64
import datetime
import json
import logging
import os
import threading
import time
import urllib
import uuid
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
from erieiron_common.aws_s3_local_cache import S3LocalCache

logging.getLogger('botocore.credentials').setLevel(logging.ERROR)


def get_asg_size(auto_scaling_group) -> Tuple[int, int, int]:
    from erieiron_config import settings
    response = boto3.client(
        "autoscaling",
        region_name=settings.AWS_DEFAULT_REGION_NAME
    ).describe_auto_scaling_groups(
        AutoScalingGroupNames=[auto_scaling_group.value]
    )

    if response["AutoScalingGroups"]:
        g = response["AutoScalingGroups"][0]
        return int(g["DesiredCapacity"]), int(g["MinSize"]), int(g["MaxSize"])
    else:
        raise ValueError(f"No Auto Scaling group not found for {auto_scaling_group.value}")


def set_asg_desired_capacity(auto_scaling_group, count: int):
    from erieiron_config import settings
    from erieiron_common import common
    current_size, min_intances, max_instances = get_asg_size(auto_scaling_group)
    count = max(min_intances, count)
    count = min(max_instances, count)

    if count == current_size:
        return

    common.log_info(f"UPDATING ASG Capacity {auto_scaling_group.value} to {count}")
    response = boto3.client(
        'autoscaling',
        region_name=settings.AWS_DEFAULT_REGION_NAME
    ).update_auto_scaling_group(
        AutoScalingGroupName=auto_scaling_group.value,
        DesiredCapacity=count
    )


def set_ecs_service_tasks(cluster_name, service_name, desired_count):
    from erieiron_config import settings
    boto3.client(
        'ecs',
        region_name=settings.AWS_DEFAULT_REGION_NAME
    ).update_service(
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
        f"#logsV2:log-groups/log-group/collaya-baremetal-logs/log-events/container-logs"
        f"$3FfilterPattern$3D$26start$3D{logging_start_time}$26end$3D{logging_end_time}"
    )


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
        logging.exception(e)
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
    response = boto3.client(
        "secretsmanager",
        region_name=os.getenv("AWS_REGION", "us-west-2")
    ).get_secret_value(
        SecretId=settings.CLOUDFRONT_PRIVATE_KEY_SECRET_NAME
    )

    return rsa.PrivateKey.load_pkcs1(response["SecretString"].encode("utf-8"))


def generate_signed_cloudfront_url(cloudfront_domain, s3_key, expires_in_seconds=604800):
    url = f"https://{cloudfront_domain}/{quote_plus(s3_key)}"
    expires = int(time.time()) + expires_in_seconds

    policy = f'''{{"Statement":[{{"Resource":"{url}","Condition":{{"DateLessThan":{{"AWS:EpochTime":{expires}}}}}}}]}}'''

    private_key = get_cloudfron_url_signing_private_key()
    signature = rsa.sign(policy.encode(), private_key, 'SHA-1')
    encoded_sig = quote_plus(base64.b64encode(signature).decode("utf-8"))

    url = f"{url}?Expires={expires}&Signature={encoded_sig}&Key-Pair-Id={settings.CLOUDFRONT_KEY_PAIR_ID}"

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
        s3_client = boto3.client(
            's3',
            region_name=os.getenv("AWS_REGION", "us-west-2")
        )

        self.s3_passthrough_cache = S3LocalCache(s3_client)

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
        from erieiron_config import settings
        transcribe = boto3.client(
            'transcribe',
            region_name=settings.AWS_DEFAULT_REGION_NAME
        )

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
        from erieiron_config import settings
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
            region_name=settings.AWS_DEFAULT_REGION_NAME
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

    def send_email(self, subject, recipient, body, sender=None):
        from erieiron_config import settings
        from erieiron_common import common
        common.log_info(f"sending email: {subject} to {recipient}")

        sender = common.default_str(sender, settings.FEEDBACK_EMAIL)
        ses_client = boto3.client('ses', region_name=settings.AWS_DEFAULT_REGION_NAME)

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
        from erieiron_config import settings

        if not person:
            raise ValueError("missing person")
        if not message_type:
            raise ValueError("missing message type")
        payload = payload or {}

        apigateway_client = boto3.client(
            'apigatewaymanagementapi',
            region_name=settings.AWS_DEFAULT_REGION_NAME,
            endpoint_url=f"https://{settings.CLIENT_MESSAGE_WEBSOCKET_ENDPOINT}"
        )

        table = boto3.resource(
            'dynamodb',
            region_name=settings.AWS_DEFAULT_REGION_NAME
        ).Table(
            settings.CLIENT_MESSAGE_DYNAMO_TABLE
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
