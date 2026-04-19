import os
import pprint
import uuid
from collections import defaultdict
from functools import wraps
from pathlib import Path

from erieiron_common import aws_utils, common


class TestAwsInterface(aws_utils.AwsInterface):
    def __init__(self, transcription_path=None):
        super().__init__()

        self.uploaded_files = {}
        if transcription_path is None:
            self.transcription_content = None
        else:
            self.transcription_content = common.read_json(transcription_path)

        self.emails = defaultdict(list)

    def send_client_message(self, person, message, payload):
        common.log_info("sending client message", person.id, message, payload)

    def assert_test_interface(self):
        pass

    def generate_presigned_url(self, bucket: str, file_key: str, content_type: str, expiration_minutes=1):
        return f"https://fakeurl/{bucket}/{file_key}"

    def set_transcription_content(self, file_path):
        self.transcription_content = common.read_json(file_path)

    def transcribe_audio(self, bucket, key):
        return self.transcription_content

    def reset_emails(self):
        self.emails = defaultdict(list)

    def _get_name(self, obj_type, s3_key, dict_id_name):
        obj_id = s3_key.split(".")[0]
        try:
            ext = s3_key.split(".")[1]
        except:
            ext = "none"

        return common.sanitize_filename(f'{obj_type}-{common.get(dict_id_name, obj_id, s3_key)}.{ext}')

    def send_email(self, subject, recipient, body, sender=None):
        self.emails[recipient].append({
            "subject": subject,
            "body": body,
            "sender": "erieironllc@gmail.com"
        })

    def assert_object_exists(self, bucket: str, key: str):
        if common.get(self.uploaded_files, [bucket, key]) is None:
            raise Exception(f'fakes3://{bucket}/{key} does not exist')

    def download(self, bucket: str, input_file_key: str) -> str:
        print(f"DOWNLOAD {bucket}/{input_file_key}")
        f = common.get(self.uploaded_files, [bucket, input_file_key])
        if f is None:
            pprint.pprint(self.uploaded_files)
            raise Exception(f"NOT FOUND {bucket}/{input_file_key}")
        else:
            if not Path(f).exists():
                raise Exception(f"downloaded file not {bucket}/{input_file_key}; {f}")

            return f

    def upload_file(
            self,
            file_path: str,
            dest_bucket: str,
            dest_key: str,
            content_type: str = None,
            asyncronous: bool = False,
            delete_when_done: bool = False
    ):
        print(f"UPLOAD {dest_bucket}/{dest_key}")
        import shutil

        local_file = common.create_temp_file(f"{dest_key}")

        if file_path != local_file:
            shutil.copyfile(file_path, local_file)

        common.struct_set(self.uploaded_files, [dest_bucket, dest_key], local_file)
        if not Path(local_file).exists():
            raise Exception(f"file does not exist {local_file}")


def non_prod_required(function):
    @wraps(function)
    def wrap(*args, **kwargs):
        if os.getenv("ERIEIRON_RUNTIME_PROFILE", "").strip().lower() == "aws":
            raise Exception("cannot use test utils from the aws runtime")

        return function(*args, **kwargs)

    return wrap


@non_prod_required
def get_project():
    from erieiron_common.models import Project
    c_id = uuid.uuid4()
    c = Project(
        id=c_id,
        name=f"convo {c_id}",
        person=generate_people()[0]
    )
    c.save()
    return c


@non_prod_required
def generate_person():
    return generate_people()[0]


@non_prod_required
def generate_people(count=1):
    from erieiron_common.models import Person
    people = []
    for i in range(count):
        person_id = uuid.uuid4()
        person = Person(
            id=person_id,
            cognito_sub=person_id,
            name=f"Test Person {person_id}",
            email=f"testperson{person_id}@erieironllc.com"
        )
        person.save()
        people.append(person)

    return people
