import difflib
import json
import logging
import subprocess
import sys
import threading
import uuid
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Tuple, Optional

from django.contrib.postgres.search import SearchVectorField, SearchVector
from django.db import models, connection, transaction
from django.db.models import QuerySet, Sum
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils import timezone

import settings
from erieiron_common import common
from erieiron_common import gpu_utils
from erieiron_common.aws_utils import get_cloudwatch_url
from erieiron_common.common import get_minutes_ago, get_now
from erieiron_common.enums import Role, ConsentChoice, PromptIntent, PubSubHandlerInstanceStatus, SystemCapacity, PubSubMessagePriority, PubSubMessageType, PubSubMessageStatus, AutoScalingGroup, ScaleAction, BusinessIdeaSource, BusinessStatus, Level, BusinessGuidanceRating, TrafficLight, GoalStatus, TaskAssigneeType, TaskStatus, LlcStructure, TaskExecutionType, TaskPhase, TaskExecutionMode, TaskExecutionSchedule, PersonAuthStatus, InitiativeType
from erieiron_common.gpu_utils import ComputeDevice
from erieiron_common.json_encoder import ErieIronJSONEncoder


class ErieIronModelBase(models.base.ModelBase):
    def __new__(cls, name, bases, attrs, **kwargs):
        meta = attrs.get('Meta', type('Meta', (), {}))
        if not getattr(meta, 'abstract', False) and not getattr(meta, 'db_table', None):
            setattr(meta, 'db_table', f'erieiron_{name.lower()}')
            attrs['Meta'] = meta
        return super().__new__(cls, name, bases, attrs, **kwargs)


class BaseErieIronModel(models.Model, metaclass=ErieIronModelBase):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class Person(BaseErieIronModel):
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

    def get_auth_status(self) -> PersonAuthStatus:
        return PersonAuthStatus.ALL_GOOD


class Project(BaseErieIronModel):
    NEW_PROJECT_NAME = "New Project"

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
    name = models.TextField(primary_key=True, editable=False)
    value = models.TextField(null=True)


class CacheData(models.Model):
    key = models.CharField(primary_key=True, max_length=1024)
    val = models.TextField()


class ProjectInteraction(BaseErieIronModel):
    RICHRESPONSE_PREFIX = "RICHRESPONSE:"

    INTERACTION_PLACEHOLDER = 'interaction_placeholder'
    SYSTEM_PROJECT = 'erieiron_system'
    FEATURE_CONFIRMATION = 'feature_confirmation'

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


class ProjectInteractionFeature(BaseErieIronModel):
    project_interaction = models.ForeignKey(ProjectInteraction, on_delete=models.CASCADE)
    name = models.TextField(null=False)
    value = models.TextField(null=False)


class PubSubMessage(BaseErieIronModel):
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
        return f"{self.get_age()}\t{self.status}: {self.get_job_name()}"

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


class PubSubEnvironment(BaseErieIronModel):
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


class PubSubHanderInstance(BaseErieIronModel):
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


class PubSubHanderInstanceProcess(BaseErieIronModel):
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


class Business(BaseErieIronModel):
    name = models.TextField(unique=True)
    source = models.TextField(null=False, choices=BusinessIdeaSource.choices())
    status = models.TextField(default=BusinessStatus.IDEA, choices=BusinessStatus.choices())

    sandbox_dir_name = models.TextField(null=False)
    service_token = models.TextField(null=True)
    summary = models.TextField(null=True)
    raw_idea = models.TextField(null=True)
    bank_account_id = models.TextField(null=True)
    business_plan = models.TextField(null=True)
    value_prop = models.TextField(null=True)
    revenue_model = models.TextField(null=True)
    audience = models.TextField(null=True)
    core_functions = models.JSONField(default=list)
    execution_dependencies = models.JSONField(default=list)
    growth_channels = models.JSONField(default=list)
    personalization_options = models.JSONField(default=list)
    allow_autonomous_shutdown = models.BooleanField(default=True)
    autonomy_level = models.TextField(null=True, choices=Level.choices())
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def get_iam_role_name(self):
        return f"erieiron-{self.service_token}-role"

    def get_sandbox_dir(self) -> Path:
        p = settings.BUSINESS_SANDBOX_ROOTDIR / self.sandbox_dir_name
        p.mkdir(parents=True, exist_ok=True)

        return p

    def get_latest_board_guidance(self) -> 'BusinessGuidance':
        return self.businessguidance_set.order_by("created_timestamp").last()

    def get_latest_capacity(self) -> 'BusinessCapacityAnalysis':
        return self.businesscapacityanalysis_set.order_by("created_timestamp").last()

    def get_latest_analysist(self) -> tuple['BusinessAnalysis', 'BusinessLegalAnalysis']:
        return self.businessanalysis_set.order_by("created_timestamp").last(), self.businesslegalanalysis_set.order_by("created_timestamp").last()

    @staticmethod
    def get_erie_iron_business() -> 'Business':
        return Business.objects.get_or_create(
            name="Erie Iron, LLC",
            defaults={
                "source": BusinessIdeaSource.HUMAN,
                "sandbox_dir_name": "erieiron"
            }
        )[0]

    def needs_bank_balance_update(self):
        return not self.businessbankbalancesnapshot_set.filter(created_timestamp__gt=common.get_now() - timedelta(days=1)).exists()

    def needs_capacity_analysis(self):
        """
        Returns True if no BusinessCapacityAnalysis has been created in the past 1 hours.
        """
        return not self.businesscapacityanalysis_set.filter(created_timestamp__gt=common.get_now() - timedelta(hours=1)).exists()

    def needs_analysis(self):
        """
        Returns True if no BusinessAnalysis has been created in the past 14 days.
        """
        return not self.businessanalysis_set.filter(created_timestamp__gt=common.get_now() - timedelta(days=14)).exists()

    def get_human_capacity(self):
        # TODO make this real
        return {
            "timestamp": "2025-06-10T18:00:00Z",
            "active_humans": [
                {
                    "id": "jj",
                    "name": "JJ",
                    "available_hours_per_week": 10,
                    "current_task_load": 0,
                    "tasks_pending": 0,
                    "status": "HAS_CAPACITY"
                }
            ],
            "total_human_capacity_hours": 18,
            "total_pending_task_hours": 0,
            "capacity_utilization_percent": 0
        }

    def get_new_business_budget_capacity(self):
        bank_balance = self.businessbankbalancesnapshot_set.order_by("created_timestamp").last()
        if not bank_balance:
            return {
                "status": "bank balance is unknown"
            }
        else:
            return bank_balance.get_aggregate_balance_data(.5)

    def get_budget_capacity(self):
        bank_balance = self.businessbankbalancesnapshot_set.order_by("created_timestamp").last()
        if not bank_balance:
            return {
                "status": "bank balance is unknown"
            }
        else:
            return bank_balance.get_aggregate_balance_data()

        # todo make this real
        # return {
        #     "timestamp": "2025-06-11T18:00:00Z",
        #     "cash_on_hand_usd": 12432.75,
        #     "monthly_burn_rate_usd": 2800.00,
        #     "runway_months": 4.4,
        #     "committed_monthly_expenses": {
        #         "aws": 700,
        #         "contractors": 1200,
        #         "software_tools": 300,
        #         "other": 600
        #     },
        #     "forecasted_revenue_usd": {
        #         "next_30_days": 350.00,
        #         "next_90_days": 1100.00
        #     },
        #     "available_budget_for_new_investments_usd": 2000.00
        # }

    def get_aws_capacity(self, fake_aws=False):
        # TODO make this real

        if not fake_aws:
            return {
                "compute": "virtually unlimited",
                "storage": "virtually unlimited",
            }

        return {
            "timestamp": "2025-06-10T18:00:00Z",
            "region": "us-west-2",
            "compute": {
                "ec2_instances_available": {
                    "t3.medium": 20,
                    "g4dn.xlarge": 2,
                    "c5.large": 10
                },
                "autoscaling_groups": [
                    {
                        "name": "collaya-inference-asg",
                        "desired_capacity": 2,
                        "max_capacity": 5,
                        "current_capacity": 2
                    }
                ],
                "lambda_invocations_per_minute": {
                    "limit": 1000,
                    "current_usage": 540
                },
                "ecs_clusters": [
                    {
                        "name": "collaya-prod",
                        "running_tasks": 12,
                        "available_capacity_percent": 40
                    }
                ]
            },
            "gpu": {
                "available_gpus": {
                    "g4dn.xlarge": 2,
                    "p3.2xlarge": 0
                },
                "inference_queue_length": 3,
                "expected_wait_time_seconds": 120
            },
            "storage": {
                "s3": {
                    "total_objects": 120000,
                    "total_size_gb": 310.4
                },
                "ebs": {
                    "total_volumes": 12,
                    "used_storage_gb": 240
                },
                "efs": {
                    "used_storage_gb": 38
                }
            },
            "network": {
                "api_gateway": {
                    "requests_per_second": {
                        "limit": 5000,
                        "current_usage": 1300
                    }
                },
                "bandwidth_mbps": {
                    "ingress": 220,
                    "egress": 180
                }
            },
            "cost": {
                "month_to_date_spend_usd": 342.18,
                "forecasted_monthly_spend_usd": 720,
                "budget_limit_usd": 1000
            },
            "alerts": [
                "Low GPU availability in us-west-2",
                "Lambda usage > 50% of quota",
                "S3 nearing 90% of cost allocation warning threshold"
            ]
        }

    def get_kpis_status(self):
        kpis = self.businesskpi_set.order_by("created_timestamp")
        if not kpis.exists():
            kpis_status = {"summary": "No KPIs defined"}
        else:
            kpis_status = {
                "summary": "\n".join(
                    f"{k.name} (kpi_id={k.kpi_id}): latest value = {BusinessKPIProgress.objects.filter(kpi=k).order_by('-created_timestamp').first().value if BusinessKPIProgress.objects.filter(kpi=k).exists() else 'N/A'}"
                    for k in kpis
                )
            }

        return kpis_status

    def get_goals_status(self):
        goals = self.businessgoal_set.order_by("created_timestamp")
        if not goals.exists():
            goals_status = {"summary": "No Goals defined"}
        else:
            goals_status = {
                "summary": "\n".join(
                    f"{g.goal_id} (kpi_id={g.kpi.kpi_id}): {g.status}"
                    for g in goals
                )
            }

        return goals_status


class BusinessAnalysis(BaseErieIronModel):
    business = models.ForeignKey("Business", on_delete=models.CASCADE)
    created_timestamp = models.DateTimeField(auto_now_add=True)
    business_name = models.TextField()
    estimated_monthly_revenue_at_steady_state_usd = models.FloatField(null=True)
    time_to_profit_estimate_months = models.IntegerField(null=True)
    potential_mode = models.TextField(null=True)
    summary = models.TextField(null=True)

    final_recommendation_justification = models.TextField(null=True)
    final_recommendation_score_1_to_10 = models.IntegerField(null=True)

    estimated_operating_total_cost_per_month_usd = models.FloatField(null=True)

    upfront_investment_estimated_amount_usd = models.FloatField(null=True)

    total_addressable_market_estimate_usd_per_year = models.FloatField(null=True)
    total_addressable_market_source_or_rationale = models.TextField(null=True)

    macro_trends_data = models.JSONField(null=True, encoder=ErieIronJSONEncoder)
    risks_data = models.JSONField(null=True, encoder=ErieIronJSONEncoder)
    monthly_expenses_data = models.JSONField(null=True, encoder=ErieIronJSONEncoder)
    potential_competitors_data = models.JSONField(null=True, encoder=ErieIronJSONEncoder)
    use_of_funds_data = models.JSONField(null=True, encoder=ErieIronJSONEncoder)


class BusinessGuidance(BaseErieIronModel):
    business = models.ForeignKey("Business", on_delete=models.CASCADE)
    created_timestamp = models.DateTimeField(auto_now_add=True)
    guidance = models.TextField(choices=BusinessGuidanceRating.choices())
    justification = models.TextField(null=True)


class BusinessLegalAnalysis(BaseErieIronModel):
    business = models.ForeignKey("Business", on_delete=models.CASCADE)
    created_timestamp = models.DateTimeField(auto_now_add=True)

    approved = models.BooleanField()
    justification = models.TextField(null=True)
    required_disclaimers_or_terms = models.JSONField(null=True, encoder=ErieIronJSONEncoder)
    risk_rating = models.TextField(null=True, choices=Level.choices())
    recommended_entity_structure = models.TextField(choices=LlcStructure.choices(), null=False)
    entity_structure_justification = models.TextField(null=True)


class BusinessBankBalanceSnapshot(BaseErieIronModel):
    business = models.ForeignKey("Business", on_delete=models.CASCADE)
    created_timestamp = models.DateTimeField(auto_now_add=True)

    def get_aggregate_balance_data(self, reserve_percent=0):
        available_balance = []
        current_balance = []
        status = []
        for account in self.businessbankbalancesnapshotaccount_set.all():
            available_balance.append(account.available_balance)
            current_balance.append(account.current_balance)
            status.append(account.status)

        return {
            "available_balance": common.safe_sum(available_balance) * (1 - reserve_percent),
            "current_balance": common.safe_sum(current_balance) * (1 - reserve_percent),
            "status": common.join_with_and(list(set(status)))
        }


class BusinessBankBalanceSnapshotAccount(BaseErieIronModel):
    snapshot = models.ForeignKey("BusinessBankBalanceSnapshot", on_delete=models.CASCADE)
    account_name = models.TextField()
    account_id = models.TextField()
    available_balance = models.FloatField(null=True)
    current_balance = models.FloatField(null=True)
    status = models.TextField(null=True)


class BusinessCapacityAnalysis(BaseErieIronModel):
    business = models.ForeignKey("Business", on_delete=models.CASCADE)
    created_timestamp = models.DateTimeField(auto_now_add=True)

    cash_capacity_status = models.TextField(choices=TrafficLight.choices())
    compute_capacity_status = models.TextField(choices=TrafficLight.choices())
    human_capacity_status = models.TextField(choices=TrafficLight.choices())
    recommendation = models.TextField(choices=TrafficLight.choices())

    justification = models.TextField(null=True)


class BusinessKPI(BaseErieIronModel):
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    created_timestamp = models.DateTimeField(auto_now_add=True)
    kpi_id = models.TextField()
    name = models.TextField()
    description = models.TextField(null=True)
    target_value = models.FloatField()
    unit = models.TextField()
    priority = models.TextField(choices=Level.choices())


class BusinessKPIProgress(BaseErieIronModel):
    kpi = models.ForeignKey(BusinessKPI, on_delete=models.CASCADE)
    created_timestamp = models.DateTimeField(auto_now_add=True)
    value = models.FloatField()


class BusinessGoal(BaseErieIronModel):
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    created_timestamp = models.DateTimeField(auto_now_add=True)
    goal_id = models.TextField()
    kpi = models.ForeignKey(BusinessKPI, on_delete=models.CASCADE)
    description = models.TextField()
    target_value = models.FloatField()
    unit = models.TextField()
    due_date = models.DateField()
    priority = models.TextField(choices=Level.choices())
    status = models.TextField(choices=GoalStatus.choices())


class BusinessCeoDirective(BaseErieIronModel):
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    created_timestamp = models.DateTimeField(auto_now_add=True)
    target_agent = models.TextField()
    directive_summary = models.TextField()
    goal_alignment = models.JSONField(default=list)
    kpi_targets = models.JSONField(default=dict)
    initiative_reference = models.TextField(default="")


class Initiative(BaseErieIronModel):
    id = models.TextField(primary_key=True)  # initiative_token
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    initiative_type = models.TextField(choices=InitiativeType.choices(), default=InitiativeType.PRODUCT)
    created_timestamp = models.DateTimeField(auto_now_add=True)
    title = models.TextField()
    description = models.TextField()
    priority = models.TextField(choices=Level.choices())
    linked_kpis = models.ManyToManyField("BusinessKPI", related_name="initiatives", blank=True)
    linked_goals = models.ManyToManyField("BusinessGoal", related_name="initiatives", blank=True)
    expected_kpi_lift = models.JSONField(default=dict)
    requires_unit_tests = models.BooleanField(default=True)


class ProductRequirement(BaseErieIronModel):
    id = models.TextField(primary_key=True)  # requirement_token
    initiative = models.ForeignKey(Initiative, on_delete=models.CASCADE, related_name='requirements')
    product_initiative = models.TextField(null=True)
    created_timestamp = models.DateTimeField(auto_now_add=True)
    summary = models.TextField()
    acceptance_criteria = models.TextField()
    testable = models.BooleanField(default=True)


class Task(BaseErieIronModel):
    id = models.TextField(primary_key=True)
    created_timestamp = models.DateTimeField(auto_now_add=True)
    initiative = models.ForeignKey(Initiative, on_delete=models.CASCADE, related_name="tasks")
    product_initiative = models.TextField(null=True)
    status = models.TextField(null=False, choices=TaskStatus.choices())
    validated_requirements = models.ManyToManyField(ProductRequirement, blank=True, related_name="validation_tasks")
    description = models.TextField()
    depends_on = models.ManyToManyField(
        'self',
        symmetrical=False,
        related_name='dependent_tasks',
        blank=True
    )
    risk_notes = models.TextField()
    test_plan = models.TextField()
    role_assignee = models.TextField(choices=TaskAssigneeType.choices())
    completion_criteria = models.JSONField(default=list)
    comment_requests = models.JSONField(default=list)
    current_spend = models.FloatField(null=True)
    max_budget_usd = models.FloatField(null=True)
    attachments = models.JSONField(default=list)
    created_by = models.TextField(null=True)

    phase = models.TextField(choices=TaskPhase.choices(), null=False)
    task_type = models.TextField(choices=TaskExecutionType.choices(), null=True, blank=True)
    execution_mode = models.TextField(choices=TaskExecutionMode.choices(), default=TaskExecutionMode.CONTAINER, null=False)
    requires_test = models.BooleanField(default=True)
    execution_schedule = models.TextField(choices=TaskExecutionSchedule.choices(), default=TaskExecutionSchedule.ONCE)
    execution_start_time = models.DateTimeField(null=True, blank=True)
    timeout_seconds = models.IntegerField(null=True, blank=True)
    guidance = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"{self.description} - {self.id}"

    def to_dict(self):
        d = self.__dict__

        return d

    def create_execution(self, input_data=None, iteration=None) -> 'TaskExecution':
        return TaskExecution.objects.create(
            task=self,
            iteration=iteration,
            status=TaskStatus.NOT_STARTED,
            input=input_data or {}
        )

    def get_last_execution(self) -> Optional['TaskExecution']:
        return self.taskexecution_set.filter(executed_time__isnull=False).order_by("executed_time").last()

    def are_dependencies_complete(self):
        return all(dep.status == TaskStatus.COMPLETE for dep in self.depends_on.all())

    def get_work_desc(self):
        completion_criteria = "\n".join(common.ensure_list(self.completion_criteria))

        return f"""
## GOAL
{self.description}

## Completion Criteria
{self.completion_criteria}
        """

    def update_dependent_tasks(self):
        from erieiron_common.message_queue.pubsub_manager import PubSubManager
        for t in self.depends_on.filter(status__in=[TaskStatus.NOT_STARTED, TaskStatus.BLOCKED]):
            PubSubManager.publish_id(PubSubMessageType.TASK_UPDATED, t.id)
        for t in self.dependent_tasks.filter(status__in=[TaskStatus.NOT_STARTED, TaskStatus.BLOCKED]):
            PubSubManager.publish_id(PubSubMessageType.TASK_UPDATED, t.id)

    def allow_execution(self):
        b = self.initiative.business
        if Business.get_erie_iron_business().id == b.id:
            return True

        return BusinessStatus.ACTIVE.eq(self.initiative.business.status)


# Design system and handoff models
class DesignComponent(BaseErieIronModel):
    id = models.TextField(primary_key=True)
    name = models.TextField()
    description = models.TextField(null=True)


class TaskExecution(BaseErieIronModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE)
    iteration = models.ForeignKey("SelfDrivingTaskIteration", on_delete=models.CASCADE, null=True)
    status = models.TextField(default=TaskStatus.NOT_STARTED, null=False, choices=TaskStatus.choices())
    created_time = models.DateTimeField(auto_now_add=True)
    executed_time = models.DateTimeField(null=True)
    input = models.JSONField(default=dict, null=True)
    output = models.JSONField(default=dict, null=True)
    error_msg = models.TextField(null=True)

    def resolve(self, output=None, status=TaskStatus.COMPLETE, error_msg=None):
        with transaction.atomic():
            TaskExecution.objects.filter(id=self.id).update(
                status=status,
                error_msg=error_msg,
                output=output or {},
                executed_time=common.get_now()
            )

        self.refresh_from_db()
        return self


class TaskDesignRequirements(BaseErieIronModel):
    task = models.OneToOneField("Task", on_delete=models.CASCADE, related_name="design_handoff")
    component_ids = models.ManyToManyField(DesignComponent, blank=True)
    layout = models.JSONField(default=dict, null=True)
    component_tree = models.JSONField(default=dict, null=True)
    notes = models.TextField(null=True)


class SelfDrivingTask(BaseErieIronModel):
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    main_name = models.TextField(null=False)
    goal = models.TextField(null=False)
    task = models.OneToOneField("Task", on_delete=models.SET_NULL, null=True, blank=True, db_index=True)
    config_path = models.TextField(null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def rollback_to(self, iteraton: 'SelfDrivingTaskIteration'):
        # we don't want any files created in future iterations hanging around, so delete everything
        # there's prob a more efficient way to do this, but this is fine for now
        for cf in CodeFile.objects.filter(codeversion__task_iteration__task=self):
            common.quietly_delete(
                common.assert_in_sandbox(
                    self.business.get_sandbox_dir(),
                    cf.file_path
                )
            )

        for i in self.selfdrivingtaskiteration_set.order_by("timestamp"):
            i.write_to_disk()
            if i.id == iteraton.id:
                break

    def get_best_iteration(self) -> 'SelfDrivingTaskIteration':
        best = self.selfdrivingtaskbestiteration_set.order_by("timestamp").last()

        return best.iteration if best else self.get_most_recent_iteration()

    def get_most_recent_iteration(self) -> 'SelfDrivingTaskIteration':
        return self.selfdrivingtaskiteration_set.order_by("timestamp").last()

    def get_most_recent_code_version(self) -> Optional['CodeVersion']:
        last_iteration = self.get_most_recent_iteration()
        if last_iteration:
            last_code_version: CodeVersion = last_iteration.codeversion_set.first()
            if last_code_version:
                return last_code_version

        return None

    def get_most_recent_log_contents(self) -> Optional[str]:
        last_iteration = self.get_most_recent_iteration()
        if last_iteration:
            return last_iteration.log_content
        else:
            return None

    def get_cost(self) -> float:
        result = LlmRequest.objects.filter(
            task_iteration__self_driving_task=self
        ).aggregate(
            total=Sum("price")
        )
        return result["total"] or 0.0

    def iterate(self) -> 'SelfDrivingTaskIteration':
        max_version = SelfDrivingTaskIteration.objects.filter(
            self_driving_task=self
        ).aggregate(
            models.Max("version_number")
        )["version_number__max"] or 0

        with transaction.atomic():
            return SelfDrivingTaskIteration.objects.create(
                self_driving_task=self,
                version_number=max_version + 1
            )

    def get_require_tests(self) -> bool:
        return self.task and self.task.requires_test


class SelfDrivingTaskIteration(BaseErieIronModel):
    self_driving_task = models.ForeignKey(SelfDrivingTask, on_delete=models.CASCADE, null=True)
    achieved_goal = models.BooleanField(null=False, default=False)
    version_number = models.IntegerField(null=False, default=0)
    planning_model = models.TextField()
    coding_model = models.TextField()
    log_content = models.TextField()
    evaluation_json = models.JSONField(null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    def get_latest_execution(self) -> TaskExecution:
        te = self.taskexecution_set.last()

        if te:
            return te
        else:
            return self.self_driving_task.task.create_execution(iteration=self)

    def get_llm_cost(self) -> Tuple[float, int]:
        totals = self.llmrequest_set.aggregate(
            total_price=Sum('price'),
            total_tokens=Sum('token_count')
        )
        return totals['total_price'] or 0, totals['total_tokens'] or 0

    def write_to_disk(self):
        sandbox_root_dir = self.self_driving_task.business.get_sandbox_dir()
        for cv in self.codeversion_set.all():
            cv.write_to_disk(sandbox_root_dir)

    def get_code_version(self, code_file):
        code_file = CodeFile.coerce_to_codefile(code_file)

        code_version_to_modify = code_file.get_version(self)

        if not code_version_to_modify:
            code_version_to_modify = code_file.get_latest_version()

        if not code_version_to_modify:
            code_version_to_modify = code_file.init_from_codefile(
                self,
                code_file.file_path
            )

        return code_version_to_modify

    def get_previous_iteration(self):
        self.get_previous_by_timestamp()
        pass


class SelfDrivingTaskBestIteration(BaseErieIronModel):
    task = models.ForeignKey(SelfDrivingTask, on_delete=models.CASCADE, null=True)
    iteration = models.ForeignKey(SelfDrivingTaskIteration, on_delete=models.CASCADE, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)


class RunningProcess(BaseErieIronModel):
    task_execution = models.OneToOneField(TaskExecution, on_delete=models.CASCADE, related_name="process", null=True, blank=True)
    process_id = models.IntegerField(null=True, blank=True)
    container_id = models.TextField(null=True, blank=True)  # For docker processes
    execution_type = models.TextField(max_length=20, choices=[('local', 'Local'), ('docker', 'Docker')])
    log_file_path = models.TextField(null=True, blank=True)
    log_tail = models.TextField(blank=True, default="")  # Store last ~1000 chars of log
    started_at = models.DateTimeField(auto_now_add=True)
    is_running = models.BooleanField(default=True)
    terminated_at = models.DateTimeField(null=True, blank=True)

    def update_log_tail(self, max_chars=100000):
        """Update the log_tail field with the latest log content"""
        if self.log_file_path and Path(self.log_file_path).exists():
            try:
                with open(self.log_file_path, 'r') as f:
                    content = f.read()
                    self.log_tail = content[-max_chars:] if len(content) > max_chars else content
                    self.save(update_fields=['log_tail'])
            except Exception as e:
                logging.warning(f"Failed to update log tail for process {self.id}: {e}")

    def kill_process(self):
        """Kill the running process"""
        import signal
        import os

        if not self.is_running:
            return False

        self.is_running = False
        self.terminated_at = common.get_now()
        self.save(update_fields=['is_running', 'terminated_at'])

        try:
            if self.execution_type == 'docker' and self.container_id:
                # Kill docker container
                subprocess.run(['docker', 'kill', self.container_id], check=True)
            elif self.execution_type == 'local' and self.process_id:
                # Kill local process
                os.kill(self.process_id, signal.SIGTERM)

            return True
        except Exception as e:
            logging.warning(f"Failed to kill process {self.id}: {e}")
            return False


class LlmRequest(BaseErieIronModel):
    task_iteration = models.ForeignKey(SelfDrivingTaskIteration, on_delete=models.CASCADE, null=True)
    token_count = models.IntegerField()
    price = models.FloatField()
    timestamp = models.DateTimeField(auto_now_add=True)


class CodeFile(BaseErieIronModel):
    # file_path is not the primary key because code file paths change as we refactor
    # gotta be unique tho
    file_path = models.TextField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @staticmethod
    def coerce_to_codefile(code_file) -> 'CodeFile':
        if isinstance(code_file, Path):
            return CodeFile.get(code_file)
        elif isinstance(code_file, CodeFile):
            return code_file
        else:
            raise ValueError(f"{code_file} must either be a path or a CodeFile instance")

    def get_path(self) -> Path:
        return Path(self.file_path)

    def get_base_name(self) -> Path:
        return common.get_basename(self.get_path())

    def get_dir(self) -> Path:
        return self.get_path().parent

    def get_latest_version(self) -> 'CodeVersion':
        return self.codeversion_set.order_by("created_at").last()

    def get_version(self, iteration: SelfDrivingTaskIteration) -> 'CodeVersion':
        return self.codeversion_set.filter(
            task_iteration=iteration
        ).order_by("created_at").last()

    @staticmethod
    def get(code_file_path: Path) -> 'CodeFile':
        code_file_path = Path(code_file_path)
        if not code_file_path.exists():
            code_file_path.parent.mkdir(parents=True, exist_ok=True)
            code_file_path.touch()
        return CodeFile.objects.get_or_create(file_path=code_file_path)[0]

    @staticmethod
    def init_from_codefile(
            task_iteration: SelfDrivingTaskIteration,
            file_path: Path
    ) -> 'CodeVersion':
        file_path = common.assert_exists(file_path)
        return CodeFile.update_from_path(
            task_iteration,
            file_path,
            code=file_path.read_text(),
            code_instructions=f"initial code from existing file"
        )

    def update(
            self,
            task_iteration: SelfDrivingTaskIteration,
            code: str,
            code_instructions=None
    ):
        cv = CodeVersion.objects.create(
            task_iteration=task_iteration,
            code_file=self,
            code_instructions=code_instructions,
            code=code
        )

        file_path = common.assert_in_sandbox(
            task_iteration.self_driving_task.business.get_sandbox_dir(),
            self.file_path
        )

        file_path = Path(self.file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(code)

        return cv

    @staticmethod
    def update_from_path(
            task_iteration: SelfDrivingTaskIteration,
            file_path: Path,
            code: str,
            code_instructions=None
    ) -> 'CodeVersion':
        with transaction.atomic():
            code_file = CodeFile.coerce_to_codefile(file_path)
            return code_file.update(
                task_iteration=task_iteration,
                code=code,
                code_instructions=code_instructions
            )


class CodeVersion(BaseErieIronModel):
    code_file = models.ForeignKey(CodeFile, on_delete=models.CASCADE)
    task_iteration = models.ForeignKey(SelfDrivingTaskIteration, on_delete=models.CASCADE)
    code_instructions = models.JSONField(null=True)
    code = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def get_diff(self) -> str:
        try:
            previous_version = self.get_previous_by_created_at()
            diff_lines = difflib.unified_diff(
                common.default_str(previous_version.code).splitlines(),
                common.default_str(self.code).splitlines(),
                fromfile="old.py",
                tofile="new.py",
                lineterm=""
            )
            return "\n".join(diff_lines)
        except:
            return ""

    def write_to_disk(self, sandbox_root_dir=None) -> Path:
        if not sandbox_root_dir:
            sandbox_root_dir = self.task_iteration.self_driving_task.business.get_sandbox_dir()

        file_path = common.assert_in_sandbox(
            sandbox_root_dir,
            self.code_file.file_path
        )

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(self.code)

        return file_path


@receiver(pre_delete, sender=SelfDrivingTaskIteration)
def kill_running_processes_on_iteration_delete(sender, instance, **kwargs):
    """
    Kill any running processes associated with this iteration before deletion.
    """
    running_processes = RunningProcess.objects.filter(
        task_execution__iteration=instance,
        is_running=True
    )
    
    for process in running_processes:
        try:
            process.kill_process()
            logging.info(f"Killed running process {process.id} for iteration {instance.id}")
        except Exception as e:
            logging.warning(f"Failed to kill process {process.id} for iteration {instance.id}: {e}")
