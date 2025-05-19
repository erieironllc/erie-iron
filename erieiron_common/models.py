import json
import logging
import sys
import threading
import uuid
from functools import lru_cache
from typing import Tuple

from django.contrib.postgres.search import SearchVectorField, SearchVector
from django.db import models, connection, transaction
from django.db.models import QuerySet
from django.db.models.query_utils import Q
from django.utils import timezone

import gpu_utils
from aws_utils import get_cloudwatch_url
from common import get_minutes_ago, get_now
from erieiron_config import settings
from erieiron_common import common
from erieiron_common.enums import Role, ConsentChoice, PromptIntent, PubSubHandlerInstanceStatus, SystemCapacity, PubSubMessagePriority, PubSubMessageType, PubSubMessageStatus, AutoScalingGroup, ScaleAction
from erieiron_common.json_encoder import ErieIronJSONEncoder
from gpu_utils import ComputeDevice


class Person(models.Model):
    class Meta:
        db_table = "person"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cognito_sub = models.UUIDField(db_index=True, null=True, unique=True)
    name = models.TextField(null=True)
    email = models.TextField()
    role = models.TextField(choices=Role.choices(), default=Role.FREE_USER.value)
    server_root = models.TextField(null=True)
    last_updated = models.DateTimeField(null=True)

    cookie_consent = models.CharField(
        max_length=10,
        choices=ConsentChoice.choices(),
        null=True,
        blank=True,
        default=None,
    )

    analytics_cookie_consent = models.CharField(
        max_length=10,
        choices=ConsentChoice.choices(),
        null=True,
        blank=True,
        default=None,
    )

    @staticmethod
    @lru_cache
    def get_system_person() -> 'Person':
        with transaction.atomic():
            return Person.objects.get_or_create(
                name="System Account",
                email=settings.SYSTEM_ACCOUNT_EMAIL,
                role=Role.SYSTEM
            )[0]

    def has_role(self, role):
        roles_in_order = Role.roles_in_order()

        person_role_idx = roles_in_order.index(Role(self.role))
        param_role_idx = roles_in_order.index(Role(role))

        return person_role_idx >= param_role_idx

    def to_dict(self):
        a_dict = self.__dict__

        return a_dict

    def is_admin(self):
        return self.has_role(Role.ADMIN)

    def __str__(self):
        return self.name

    def get_first_name(self):
        if not self.name:
            return ""

        try:
            return self.name.split(" ")[0]
        except:
            return self.name

    @staticmethod
    def getOrCreateFromCognitoUserData(user_data) -> 'Person':
        cognito_sub = common.get(user_data, 'sub')
        assert cognito_sub

        if Person.objects.filter(cognito_sub=cognito_sub).exists():
            p = Person.objects.get(cognito_sub=cognito_sub)
            p.name = common.get(user_data, 'name')
            p.email = common.get(user_data, 'email')
        else:
            p = Person(
                cognito_sub=cognito_sub,
                name=common.get(user_data, 'name'),
                email=common.get(user_data, 'email')
            )

        p.save()
        return p


class Project(models.Model):
    class Meta:
        db_table = "project"

    NEW_PROJECT_NAME = "New Project"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.TextField(null=False, default="")
    person = models.ForeignKey(Person, on_delete=models.PROTECT)
    created_timestamp = models.DateTimeField(auto_now_add=True)
    updated_timestamp = models.DateTimeField(auto_now_add=True)
    last_interacted_timestamp = models.DateTimeField(auto_now_add=True, null=False)

    search_vector = SearchVectorField(null=True)

    def update_search_vector(self):
        if connection.vendor == 'postgresql':
            Project.objects.filter(id=self.id).update(search_vector=(
                SearchVector('name', weight='A')
            ))

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.update_search_vector()

    def to_dict(self):
        p_dict = self.__dict__

        return p_dict

    def get_interactions_for_chat_context(self, depth=5):
        return list(reversed(self.projectinteraction_set.exclude(
            feedback=False
        ).order_by("-created_timestamp")[:depth]))

    def get_most_recent_interaction(self):
        interactions = self.tail_interactions()
        if len(interactions) == 0:
            return None
        else:
            return interactions[0]

    def tail_interactions(self, count=1):
        count = max(count, 1)
        return list(reversed(self.projectinteraction_set.filter(
            arrangement_section__isnull=True
        ).order_by(
            "-created_timestamp"
        )[:count]))


class RuntimeConfigVal(models.Model):
    class Meta:
        db_table = "runtime_config"

    name = models.TextField(primary_key=True, editable=False)
    value = models.TextField(null=True)


class CacheData(models.Model):
    class Meta:
        db_table = "cachedata"

    key = models.CharField(primary_key=True, max_length=1024)
    val = models.TextField()


class ProjectInteraction(models.Model):
    RICHRESPONSE_PREFIX = "RICHRESPONSE:"

    INTERACTION_PLACEHOLDER = 'interaction_placeholder'
    SYSTEM_PROJECT = 'erieiron_system'
    FEATURE_CONFIRMATION = 'feature_confirmation'

    class Meta:
        db_table = "project_interaction"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.PROTECT)
    person = models.ForeignKey(Person, on_delete=models.PROTECT, null=True)
    prompt = models.TextField(null=True)
    response = models.TextField(null=True)
    feedback = models.BooleanField(null=True)
    channel = models.TextField(null=False, default=SYSTEM_PROJECT)
    context_maker = models.TextField(null=True)
    intent = models.TextField(choices=PromptIntent.choices(), null=False, default=PromptIntent.UNKNOWN)
    created_timestamp = models.DateTimeField(auto_now_add=True)
    parsed_prompt = models.JSONField(null=True, encoder=ErieIronJSONEncoder)

    extra_data = models.JSONField(null=True, encoder=ErieIronJSONEncoder)
    waiting = models.BooleanField(null=True, default=False)

    search_vector = SearchVectorField(null=True)

    def update_search_vector(self):
        if connection.vendor == 'postgresql':
            ProjectInteraction.objects.filter(id=self.id).update(search_vector=(
                    SearchVector('response', weight='A') +
                    SearchVector('prompt', weight='B')
            ))

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.update_search_vector()

    @staticmethod
    def create(
            *,
            project: Project,
            person: Person,
            channel: str,
            context_maker: str = None,
            prompt: str = None,
            intent: PromptIntent = PromptIntent.UNKNOWN,
            response: str = None
    ):
        with transaction.atomic():
            ci = ProjectInteraction.objects.create(
                project=project,
                person=person,
                channel=channel,
                context_maker=context_maker,
                prompt=prompt,
                intent=intent,
                response=response
            )

        return ci

    def get_previous_interactions(self) -> QuerySet['ProjectInteraction']:
        ids = [i.pk for i in self.project.tail_interactions(30)]

        return ProjectInteraction.objects.filter(
            id__in=ids
        ).filter(
            created_timestamp__lte=self.created_timestamp
        ).exclude(
            id=self.id
        ).order_by("-created_timestamp")

    def add_feature(self, name, value):
        ProjectInteractionFeature.objects.update_or_create(
            project_interaction_id=self.id,
            name=name,
            value=value
        )

        return self

    def get_response_text(self, person=None):
        text_response = self.response

        return text_response

    def __str__(self):
        return f"Interaction {self.pk}; prompt={common.default_str(self.prompt)}; {common.default_str(self.response)}"


class ProjectInteractionFeature(models.Model):
    class Meta:
        db_table = "project_interaction_feature"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project_interaction = models.ForeignKey(ProjectInteraction, on_delete=models.CASCADE)
    name = models.TextField(null=False)
    value = models.TextField(null=False)


class PubSubMessage(models.Model):
    class Meta:
        db_table = "pubsub_message"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    env = models.TextField(null=False, db_index=True)
    namespace = models.TextField(null=False, db_index=True)
    message_type = models.TextField(null=False)
    priority = models.TextField(null=True, default=PubSubMessagePriority.NORMAL.value, choices=PubSubMessagePriority.choices())
    status = models.TextField(null=False)
    payload = models.JSONField(null=False, blank=False, default=dict, encoder=ErieIronJSONEncoder)
    created_at = models.DateTimeField(auto_now_add=True, null=False)
    updated_at = models.DateTimeField(auto_now=True, null=False)
    start_time = models.DateTimeField(null=True)
    end_time = models.DateTimeField(null=True)
    handler_instance_id = models.TextField(null=True, db_index=True)
    retry_count = models.IntegerField(null=True, default=1)
    error_message = models.TextField(null=True)
    message_batch_idx = models.IntegerField(null=True)

    def get_job_name(self) -> str:
        payload: dict = self.payload
        if payload:
            if "job_runner_class_name" in payload:
                return payload["job_runner_class_name"]

        return self.message_type

    def __str__(self):
        return f"{self.get_age()}\t{self.status}: {self.get_job_name()} https://collaya.com/admin/message_queue/{self.id}"

    def get_age(self):
        created_time_delta = int((timezone.now() - self.created_at).total_seconds())
        if PubSubMessageStatus(self.status) == PubSubMessageStatus.PROCESSING:
            star_time_delta = int((timezone.now() - self.start_time).total_seconds())
            age = f"{str(created_time_delta - star_time_delta).rjust(3)}|{str(star_time_delta).ljust(3)}"
        else:
            age = f"{str(created_time_delta).rjust(3)}|{'..'.ljust(3)}"
        return age

    def __eq__(self, other):
        if not isinstance(other, PubSubMessage):
            return False
        return self.id == other.id

    def __hash__(self):
        return hash(self.id)

    def to_dict(self):
        d = self.__dict__

        d['person'] = None
        d['job_name'] = self.get_job_name()
        d['age'] = self.get_age()
        d['status'] = PubSubMessageStatus(self.status)

        d['cloudwatch_url_created_at'] = get_cloudwatch_url(self.created_at)
        d['cloudwatch_url_start_time'] = get_cloudwatch_url(self.start_time)
        d['cloudwatch_url_end_time'] = get_cloudwatch_url(self.end_time)
        d['cloudwatch_url_updated_at'] = get_cloudwatch_url(self.updated_at)
        d['cloudwatch_url_last_heard_from'] = get_cloudwatch_url(common.safe_max([
            self.updated_at,
            self.end_time,
            self.start_time,
            self.created_at
        ]))

        if self.start_time is not None:
            d['pickup_time'] = common.format_millis_to_hr_min_sec(1000 * (self.start_time - self.created_at).total_seconds(), decimal_places=1)
        else:
            d['pickup_time'] = None

        if self.start_time is not None and self.end_time is not None:
            d['process_time'] = common.format_millis_to_hr_min_sec(1000 * (self.end_time - self.start_time).total_seconds(), decimal_places=1)
            d['total_time'] = common.format_millis_to_hr_min_sec(1000 * (self.end_time - self.created_at).total_seconds(), decimal_places=1)
        else:
            d['duration'] = None
            d['total_time'] = None
        return d

    @classmethod
    def has_pending_messages(
            cls,
            env: str,
            namespace: str = None,
            message_type: PubSubMessageType = None,
            excluding_message: 'PubSubMessage' = None
    ) -> bool:
        return cls.fetch(
            env,
            status=PubSubMessageStatus.inprog_statuses(),
            message_type=message_type,
            namespace=namespace,
            excluding_message=excluding_message
        ).exists()

    @classmethod
    def fetch(
            cls,
            env: str,
            status: PubSubMessageStatus = None,
            message_type: PubSubMessageType = None,
            priority: PubSubMessagePriority = None,
            handler_instance_id: str = None,
            namespace: str = None,
            within_last_minutes: int = None,
            no_later_than_minutes_ago: int = None,
            excluding_message: 'PubSubMessage' = None,
            limit=100
    ):
        q = PubSubMessage.objects.filter(
            env__in=common.ensure_list(env)
        )

        if status:
            q = q.filter(status__in=PubSubMessageStatus.to_value_list(status))

        if priority:
            q = q.filter(priority__in=PubSubMessagePriority.to_value_list(priority))

        if message_type:
            q = q.filter(message_type__in=PubSubMessageType.to_value_list(message_type))

        if namespace:
            q = q.filter(namespace=namespace)

        if within_last_minutes is not None:
            q = q.filter(updated_at__gte=get_minutes_ago(within_last_minutes))

        if no_later_than_minutes_ago is not None:
            q = q.filter(updated_at__lte=get_minutes_ago(no_later_than_minutes_ago))

        if excluding_message:
            q = q.exclude(id=excluding_message.id)

        if limit is not None:
            return q.order_by("-created_at")[0:limit]
        else:
            return q.order_by("-created_at")

    @classmethod
    def get_pending_messages(
            cls,
            env,
            namespace=None,
            message_type: PubSubMessageType = None,
            limit=100
    ) -> QuerySet['PubSubMessage']:
        return cls.fetch(
            status=[PubSubMessageStatus.PENDING.value, PubSubMessageStatus.PROCESSING.value],
            env=env,
            namespace=namespace,
            message_type=message_type,
            limit=limit
        )

    @classmethod
    def has_failed_messages(
            cls,
            env,
            namespace=None,
            message_type: PubSubMessageType = None,
            limit=100) -> bool:
        return cls.get_failed_messages(
            env=env,
            namespace=namespace,
            message_type=message_type,
            limit=limit
        ).exists()

    @classmethod
    def get_failed_messages(
            cls,
            env,
            namespace=None,
            message_type: PubSubMessageType = None,
            limit=100
    ) -> QuerySet['PubSubMessage']:
        return cls.fetch(
            status=PubSubMessageStatus.FAILED,
            env=env,
            namespace=namespace,
            message_type=message_type,
            limit=limit
        )

    @classmethod
    def delete_by_namespace(cls, namespace):
        if not namespace:
            raise ValueError("no namespace supplied")

        _, deleted_count = PubSubMessage.objects.filter(namespace=namespace).delete()

        return deleted_count

    @classmethod
    def has_hung_messages(cls, env):
        return cls.get_hung_messages(env).exists()

    @classmethod
    def get_hung_messages(cls, env, limit=100) -> QuerySet['PubSubMessage']:
        return cls.fetch(
            env=env,
            status=PubSubMessageStatus.PROCESSING,
            no_later_than_minutes_ago=15,
            limit=limit
        )

    @classmethod
    def create(
            cls,
            env: str,
            message_type: PubSubMessageType,
            namespace: str,
            payload: dict,
            priority: PubSubMessagePriority = PubSubMessagePriority.NORMAL,
            batch_idx=None
    ) -> 'PubSubMessage':
        return PubSubMessage.objects.create(
            env=env,
            namespace=namespace,
            message_type=message_type,
            status=PubSubMessageStatus.PENDING,
            payload=payload,
            priority=priority,
            start_time=None,
            end_time=None,
            handler_instance_id=None,
            message_batch_idx=batch_idx,
            error_message=None
        )

    @classmethod
    def reprocess(cls, message_ids, env):
        if isinstance(env, str):
            env = PubSubEnvironment.objects.get(id=env)

        for msg in PubSubMessage.objects.filter(id__in=common.ensure_list(message_ids)):
            common.log_info("reprocessing", msg.id)

            with transaction.atomic():
                PubSubMessage.objects.filter(id=msg.id).update(
                    status=PubSubMessageStatus.PENDING,
                    env=env.id if env else msg.env,
                    handler_instance_id=None,
                    retry_count=0,
                    error_message=None,
                    start_time=None,
                    end_time=None
                )

    @classmethod
    def mark_processing(cls, message_id, handler_instance_id):
        with transaction.atomic():
            PubSubMessage.objects.filter(id=message_id).update(
                status=PubSubMessageStatus.PROCESSING,
                start_time=get_now(),
                handler_instance_id=handler_instance_id,
                end_time=None
            )

    @classmethod
    def mark_finished(
            cls,
            message_id,
            status,
            handler_instance_id,
            error_message=None
    ):
        status = PubSubMessageStatus(status)

        if status not in [
            PubSubMessageStatus.FAILED,
            PubSubMessageStatus.NO_CONSUMER,
            PubSubMessageStatus.OBSOLETE,
            PubSubMessageStatus.PROCESSED
        ]:
            raise ValueError(f"invalid finish status: {status}")

        with transaction.atomic():
            PubSubMessage.objects.filter(id=message_id).update(
                status=status,
                end_time=get_now(),
                handler_instance_id=handler_instance_id,
                error_message=error_message
            )

    @classmethod
    def has_pending_messages_for_id(
            cls,
            namespace_id,
            env,
            message_types: PubSubMessageType = None,
            excluding_message=None
    ) -> bool:
        if message_types is None:
            message_types = PubSubMessageType
        else:
            message_types = common.ensure_list(message_types)

        for message_type in message_types:
            if PubSubMessage.has_pending_messages(
                    env=env,
                    namespace=message_type.get_namespace(namespace_id),
                    message_type=message_type,
                    excluding_message=excluding_message
            ):
                return True

        return False

    def print_prending_messages(self, message_type: PubSubMessageType, namespace_id):
        namespace = message_type.get_namespace(namespace_id)

        pending_messages = PubSubMessage.get_pending_messages(
            env=self.env,
            namespace=namespace,
            message_type=message_type
        )
        if len(pending_messages) == 0:
            common.log_info(f"PUB SUB all messages are complete: {self.env} {namespace}")
        else:
            common.log_info(f"PUB SUB waiting until messages are complete: {self.env} {namespace}")
            for m in pending_messages:
                mt = PubSubMessageType(m.message_type)

                try:
                    payload = json.loads(m.payload)
                except:
                    payload = None

                common.log_info(f"\t\tWAITING FOR\t\t {self.env} {namespace} blocked by {m.message_type} {m.id}")


class PubSubEnvironment(models.Model):
    class Meta:
        db_table = "pubsub_environment"

    id = models.TextField(primary_key=True, editable=False)
    desired_handler_instance_count = models.IntegerField(null=False, default=1)
    last_requested_increase = models.DateTimeField(null=True, auto_now_add=True)

    def __str__(self):
        return self.id

    def set_desired_instance_count(self, new_capacity, why):
        common.log_info(f"env {self.id} adjuting ASG scale to {new_capacity}: {why}")
        new_capacity = int(new_capacity)

        PubSubEnvironment.objects.filter(id=self.id).update(
            desired_handler_instance_count=new_capacity,
            last_requested_increase=get_now()
        )
        self.refresh_from_db()

        if self.id == "production":
            from erieiron_common import aws_utils
            aws_utils.set_asg_desired_capacity(
                AutoScalingGroup.MESSAGE_PROCESSOR,
                new_capacity
            )


class PubSubHanderInstance(models.Model):
    class Meta:
        db_table = "pubsub_message_handler_instance"

    id = models.TextField(primary_key=True, editable=False)
    environment = models.ForeignKey(PubSubEnvironment, null=False, on_delete=models.PROTECT)
    env = models.TextField(null=False, db_index=True, default=settings.MESSAGE_QUEUE_ENV)
    instance_status = models.TextField(null=False, default=PubSubHandlerInstanceStatus.NOT_AVAILABLE)
    max_db_connections = models.IntegerField(null=False, default=0)
    used_db_connections = models.IntegerField(null=False, default=0)
    system_capacity = models.TextField(null=True)
    system_capacity_explanation = models.TextField(null=True)
    mem_percent = models.IntegerField(null=False, default=0)
    cpu_percent = models.IntegerField(null=False, default=0)
    gpu_percent = models.IntegerField(null=True, default=0)
    compute_device = models.TextField(null=True, choices=ComputeDevice.choices())
    first_heard_from = models.DateTimeField(null=True, auto_now_add=True)
    last_heard_from = models.DateTimeField(null=True, auto_now_add=True)

    job_limits_def = models.TextField(null=True)
    desired_process_count = models.IntegerField(null=True, default=0)
    threads_per_process = models.IntegerField(null=True, default=3)

    lock = threading.Lock()

    def to_dict(self):
        d = self.__dict__
        d['system_capacity'] = SystemCapacity(self.system_capacity) if self.system_capacity else None
        d['instance_status'] = PubSubHandlerInstanceStatus(self.instance_status)
        return d

    def __str__(self):
        return f"{self.id} env={self.environment_id}"

    def get_messages(self, status=None, message_type=None, limit=100):
        return PubSubMessage.fetch(
            status=status,
            env=self.env,
            message_type=message_type,
            handler_instance_id=self.id,
            limit=limit
        )

    def get_inprogress_messages(self) -> Tuple[list[PubSubMessage], list[PubSubMessage]]:
        messages = self.get_messages(status=PubSubMessageStatus.PROCESSING)

        inprogress_messages = []
        hung_messages = []
        for m in messages:
            last_touched = int((timezone.now() - m.updated_at).total_seconds())
            if last_touched < 600:
                inprogress_messages.append(m)
            else:
                hung_messages.append(m)

        return inprogress_messages, hung_messages

    def get_current_threadcount(self):
        messages = self.get_messages(status=PubSubMessageStatus.PROCESSING)
        count = 0
        for m in messages:
            last_touched = int((timezone.now() - m.updated_at).total_seconds())
            if last_touched < 600:
                count += 1
        return count

    def is_drain_requested(self):
        return PubSubHandlerInstanceStatus(self.instance_status) != PubSubHandlerInstanceStatus.AVAILABLE

    def delete_handler(self):
        self.set_status(PubSubHandlerInstanceStatus.NOT_AVAILABLE)
        PubSubMessage.reprocess([msg.id for msg in self.get_messages(status=PubSubMessageStatus.PROCESSING)], self.env)
        PubSubHanderInstance.objects.filter(id=self.id).delete()

    def set_status(self, status: PubSubHandlerInstanceStatus):
        with transaction.atomic():
            self.instance_status = PubSubHandlerInstanceStatus(status).value
            instance = PubSubHanderInstance.objects.select_for_update().get(id=self.id)
            instance.instance_status = PubSubHandlerInstanceStatus(status).value
            instance.compute_device = gpu_utils.get_compute_device()
            instance.save(update_fields=["instance_status", "compute_device"])
        return self

    def ping(self, system_capacity: SystemCapacity, system_capacity_explanation: str):
        from message_queue.resource_manager import get_db_connections_info
        max_connections, used_connections = get_db_connections_info()

        with transaction.atomic():
            PubSubHanderInstance.objects.filter(id=self.id).update(
                used_db_connections=used_connections,
                max_db_connections=max_connections,
                last_heard_from=get_now(),
                system_capacity=system_capacity,
                system_capacity_explanation=system_capacity_explanation,
                gpu_percent=common.get_gpu_used_percent(),
                cpu_percent=common.get_cpu_used_percent(),
                mem_percent=common.get_memory_used_percent()
            )

    @staticmethod
    def cancel_thread_drain(instance_id):
        handler = PubSubHanderInstance.objects.get(id=instance_id)
        handler.set_status(PubSubHandlerInstanceStatus.AVAILABLE)

    @staticmethod
    def drain_instance(instance_id, instance_status=None):
        common.log_info("request_thread_drain", instance_id)
        try:
            handler = PubSubHanderInstance.objects.get(id=instance_id)

            handler.set_status(common.default(instance_status, PubSubHandlerInstanceStatus.NOT_AVAILABLE))

            inprog_messages, hung_messages = handler.get_inprogress_messages()
            PubSubMessage.reprocess([m.id for m in hung_messages], handler.env)
            PubSubMessage.reprocess([m.id for m in inprog_messages], handler.env)

            handler.pubsubhanderinstanceprocess_set.all().delete()
        except PubSubHanderInstance.DoesNotExist as e:
            logging.exception(e)

    def get_messages_for_host(self, status: PubSubMessageStatus) -> QuerySet['PubSubMessage']:
        return PubSubMessage.objects.filter(
            env=self.env,
            handler_instance_id=self.id,
            status__in=PubSubMessageStatus.to_value_list(status)
        )

    def get_inprogress_message_ids(self) -> list:
        messages_ids = []
        for p in self.pubsubhanderinstanceprocess_set.all():
            messages_ids += p.get_inprogress_message_ids()
        return common.filter_none(messages_ids)

    @classmethod
    def get_or_create(cls, env: PubSubEnvironment, instance_id) -> 'PubSubHanderInstance':
        with cls.lock:
            with transaction.atomic():
                if not PubSubHanderInstance.objects.filter(id=instance_id).exists():
                    PubSubHanderInstance.objects.create(
                        id=instance_id,
                        environment=env,
                        env=env.id,
                        compute_device=gpu_utils.get_compute_device(),
                        first_heard_from=get_now(),
                        last_heard_from=get_now()
                    )
                else:
                    PubSubHanderInstance.objects.filter(id=instance_id).update(
                        environment=env,
                        env=env.id,
                        compute_device=gpu_utils.get_compute_device(),
                        last_heard_from=get_now()
                    )

        return PubSubHanderInstance.objects.get(id=instance_id)

    @classmethod
    def cleanup_dead_instances(cls, env):
        for dead_instance in PubSubHanderInstance.objects.filter(
                env=env,
                id__startswith="i-",
                instance_status=PubSubHandlerInstanceStatus.AVAILABLE,
                last_heard_from__lt=get_minutes_ago(2)
        ):
            PubSubHanderInstance.drain_instance(
                dead_instance.id,
                instance_status=PubSubHandlerInstanceStatus.KILL_REQUESTED
            )

    @classmethod
    def get_instance_scaling_needs(cls, environment: PubSubEnvironment, current_asg_capacity=None) -> Tuple[ScaleAction, int]:
        from erieiron_common import aws_utils
        if current_asg_capacity is None:
            current_asg_capacity, min_size, max_size = aws_utils.get_asg_size(AutoScalingGroup.MESSAGE_PROCESSOR)

        baremetal_instance_count = PubSubHanderInstance.objects.filter(
            compute_device=ComputeDevice.CUDA,
            instance_status=PubSubHandlerInstanceStatus.AVAILABLE,
            env=environment
        ).exclude(
            id__startswith="i-"
        ).count()

        count_cudaonly_pending = PubSubMessage.fetch(
            env=environment,
            message_type=PubSubMessageType.get_cuda_only_message_types(),
            status=PubSubMessageStatus.PENDING
        ).count()

        total_cuda_capacity = baremetal_instance_count + current_asg_capacity
        if count_cudaonly_pending > 0 and total_cuda_capacity == 0:
            return ScaleAction.INCREASE

        count_pending = PubSubMessage.fetch(
            env=environment,
            status=PubSubMessageStatus.PENDING
        ).count()

        count_updated_last_10mins = PubSubMessage.fetch(
            env=environment,
            priority=[PubSubMessagePriority.NORMAL, PubSubMessagePriority.LOW],
            within_last_minutes=10
        ).count()

        count_non_high_priority = PubSubMessage.fetch(
            env=environment,
            priority=[PubSubMessagePriority.NORMAL, PubSubMessagePriority.LOW],
            status=PubSubMessageStatus.inprog_statuses()
        ).count()

        if count_updated_last_10mins == 0 and count_non_high_priority == 0:
            return ScaleAction.GO_TO_ZERO
        elif count_non_high_priority > 20:
            return ScaleAction.INCREASE
        elif count_pending < 100:
            return ScaleAction.DECREASE
        else:
            return ScaleAction.NO_CHANGE

    def shutdown(self, instance_is_running_on_this_machine=False):
        with transaction.atomic():
            PubSubHanderInstance.objects.filter(id=self.id).update(
                instance_status=PubSubHandlerInstanceStatus.KILL_REQUESTED
            )
        self.refresh_from_db(fields=["instance_status"])

        messages_ids = []
        procids_to_kill = []

        for proc in self.pubsubhanderinstanceprocess_set.all():
            procids_to_kill.append(proc.id)
            inprog_messages = proc.get_inprogress_messages()
            messages_ids += [m.id for m in inprog_messages]

        PubSubMessage.reprocess(messages_ids, self.env)

        PubSubHanderInstanceProcess.killkillkill(procids_to_kill)
        if instance_is_running_on_this_machine:
            # killkillkill will cause the processes to be eventually cleaned up
            # but kill them here makes it happen quicker and shortens the opportunity
            # to have msg that's marked for reprocess be finished by it's process
            for pid in procids_to_kill:
                common.kill_pid(pid)


class PubSubHanderInstanceProcess(models.Model):
    class Meta:
        db_table = "pubsub_message_handler_instance_process"
        unique_together = (('handler_instance', 'pid'),)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    handler_instance = models.ForeignKey(PubSubHanderInstance, null=True, on_delete=models.PROTECT)
    pid = models.IntegerField(null=False)
    process_status = models.TextField(null=False, default=PubSubHandlerInstanceStatus.AVAILABLE)
    cmd = models.TextField(null=True)
    exclusive_priority = models.TextField(null=True, choices=PubSubMessagePriority.choices(), default=None)
    first_heard_from = models.DateTimeField(null=False, auto_now_add=True)
    last_heard_from = models.DateTimeField(null=False, auto_now_add=True)
    current_threads = models.JSONField(null=False, blank=False, default=dict, encoder=ErieIronJSONEncoder)
    lock = threading.Lock()

    def to_dict(self):
        d = self.__dict__
        d['process_status'] = PubSubHandlerInstanceStatus(self.process_status)
        d['exclusive_priority'] = PubSubMessagePriority(self.exclusive_priority) if self.exclusive_priority else None
        d['thread_datas'] = [
            {'thread_name': thread_name, 'payload': payload} for thread_name, payload in common.default(self.current_threads, {}).items()
        ]
        return d

    def __str__(self):
        return f"pid={self.pid} id={self.id}{' exclusive_priority=' + PubSubMessagePriority(self.exclusive_priority).label() if self.exclusive_priority else ''} started via \"python {self.cmd}\""

    def ping(self):
        try:
            with transaction.atomic():
                instance = self.get_for_update()
                instance.last_heard_from = get_now()
                instance.save(update_fields=["last_heard_from"])
        except PubSubHanderInstanceProcess.DoesNotExist:
            logging.info(f"unable to ping as instance no longer exists.  process_id={self.id}; instance_id{self.handler_instance_id}")

    def update_thread(self, thread_name, thread_action, message: PubSubMessage = None):
        payload = {"action": thread_action}
        if message:
            payload['message_id'] = message.id

        with self.lock:
            with transaction.atomic():
                instance = self.get_for_update()
                instance.last_heard_from = get_now()
                current_threads = instance.current_threads
                current_threads[thread_name] = payload
                instance.current_threads = current_threads
                instance.save(update_fields=["current_threads", "last_heard_from"])

    def get_for_update(self) -> 'PubSubHanderInstanceProcess':
        return PubSubHanderInstanceProcess.objects.select_for_update().get(id=self.id)

    def remove_thread(self, thread_name):
        with self.lock:
            with transaction.atomic():
                instance = self.get_for_update()
                instance.last_heard_from = get_now()
                current_threads = instance.current_threads
                if thread_name in current_threads:
                    del current_threads[thread_name]
                    instance.current_threads = current_threads
                    instance.save(update_fields=["current_threads", "last_heard_from"])

    def reset_threads(self):
        with self.lock:
            with transaction.atomic():
                instance = self.get_for_update()
                instance.last_heard_from = get_now()
                instance.current_threads = {}
                instance.save(update_fields=["current_threads", "last_heard_from"])

    def get_inprogress_messages(self) -> QuerySet['PubSubMessage']:
        return PubSubMessage.objects.filter(
            id__in=self.get_inprogress_message_ids(),
            status=PubSubMessageStatus.PROCESSING
        )

    def get_inprogress_message_ids(self) -> list:
        messages_ids = []
        for td in self.to_dict()['thread_datas']:
            mid = common.get(td, ["payload", "message_id"])
            messages_ids.append(mid)
        return common.filter_none(messages_ids)

    @classmethod
    def get_or_create(cls, handler_instance_id, pid, exclusive_priority=None) -> 'PubSubHanderInstanceProcess':
        with cls.lock:
            with transaction.atomic():
                proc, created = PubSubHanderInstanceProcess.objects.get_or_create(
                    handler_instance_id=handler_instance_id,
                    pid=pid
                )

                if created:
                    PubSubHanderInstanceProcess.objects.filter(id=proc.id).update(
                        cmd=" ".join(sys.argv),
                        exclusive_priority=exclusive_priority,
                        first_heard_from=get_now(),
                        last_heard_from=get_now()
                    )
                else:
                    PubSubHanderInstanceProcess.objects.filter(id=proc.id).update(
                        cmd=" ".join(sys.argv),
                        exclusive_priority=exclusive_priority,
                        last_heard_from=get_now()
                    )

                return proc

    def is_drain_requested(self):
        return PubSubHandlerInstanceStatus(self.process_status) != PubSubHandlerInstanceStatus.AVAILABLE

    def set_status(self, status: PubSubHandlerInstanceStatus):
        with transaction.atomic():
            self.process_status = PubSubHandlerInstanceStatus(status).value
            proc = PubSubHanderInstanceProcess.objects.select_for_update().get(id=self.id)
            proc.process_status = PubSubHandlerInstanceStatus(status).value
            proc.save(update_fields=["process_status"])
        return self

    def is_alive(self):
        if PubSubHandlerInstanceStatus.KILL_REQUESTED.eq(self.process_status):
            return False

        if self.last_heard_from < get_minutes_ago(10):
            return False

        if self.handler_instance_id == common.get_machine_name():
            # if running from same box as the process, can check the OS to
            # see if it's alive.  this is the more efficient and direct way to check
            return common.is_process_alive(self.pid)
        else:
            return True

    @classmethod
    def killkillkill(cls, process_id):
        process_ids = common.ensure_list(process_id)
        with transaction.atomic():
            PubSubHanderInstanceProcess.objects.filter(id__in=process_ids).update(
                process_status=PubSubHandlerInstanceStatus.KILL_REQUESTED
            )
