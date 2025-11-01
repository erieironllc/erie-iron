import contextlib
import inspect
import json
import logging
import os
import threading
import time
import uuid
import warnings
from collections import Counter, defaultdict
from datetime import timedelta
from functools import wraps, lru_cache, cached_property
from pathlib import Path
from threading import Thread
from typing import Dict
from typing import List

from django.core.exceptions import ObjectDoesNotExist
from django.db import connections, close_old_connections
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from erieiron_common import common, settings_common
from erieiron_common.common import get_now, get_methods_with_decorator
from erieiron_common.enums import PubSubMessageType, PubSubMessageStatus, PubSubHandlerInstanceStatus, PubSubMessagePriority, ComputeDevice
from erieiron_common.enums import SystemCapacity
from erieiron_common.models import PubSubMessage, PubSubHanderInstance, PubSubHanderInstanceProcess, PubSubEnvironment

MIN_THREADS = 1

# loop intervals (seconds)
THREAD_MANAGER_LOOP_SEC = .2
SLEEP_BACKOFF_NO_WORK = 0

# MESSAGE_HANDLER_ATTEMPT_COUNT must be at least one, otherwise we'll never do any work.
# if it's greater than one, we'll retry on error
MESSAGE_HANDLER_MAX_RETRIES = 3

subscribers = defaultdict(set)

os.environ["TREE_SITTER_SKIP_VENDOR"] = "1"
warnings.filterwarnings("ignore", category=FutureWarning, module="tree_sitter")

class ThreadShutdownException(Exception):
    pass


class ThreadManager:
    def __init__(
            self,
            max_percent_capacity_consumed,
            func_needs_threads,
            func_get_max_threads,
            func_get_min_threads=None,
            func_get_current_threads=None,
            func_on_init=None,
            func_on_destroy=None,
            func_on_loop=None,
            func_on_thread_management_complete=None
    ):
        from erieiron_common import common
        from erieiron_common import cache

        common.log_debug("creating new thread manager")
        self.func_on_loop = func_on_loop
        self.func_get_min_threads = func_get_min_threads
        self.func_get_max_threads = func_get_max_threads
        self.func_get_current_threads = func_get_current_threads
        self.func_on_init = func_on_init
        self.func_on_thread_management_complete = func_on_thread_management_complete
        self.func_on_destroy = func_on_destroy
        self.func_needs_threads = func_needs_threads

        # store shared thread‑locals for workers
        self.thread_locals = {"aws_interface": cache.tl_get("aws_interface")}

        # always start the light‑weight thread manager
        monitor_thread = Thread(
            target=_manage_threads,
            args=[self, max_percent_capacity_consumed],
            name=f"PubSub ThreadManager-{uuid.uuid4()}",
            daemon=True
        )
        monitor_thread.start()

    # stop_all and prune_threads methods removed


def _should_start_worker(tm, max_percent_capacity_consumed=70) -> bool:
    """Pure helper: decide if we may spawn a new worker."""
    from erieiron_common.message_queue.resource_manager import get_system_capacity

    system_capacity, _ = get_system_capacity(max_percent_capacity_consumed)

    needs_threads = tm.func_needs_threads()
    has_capacity = SystemCapacity.AVAILABLE.eq(system_capacity)

    should_start = has_capacity and needs_threads

    return should_start


def _manage_threads(thread_manager: 'ThreadManager', max_percent_capacity_consumed=70):
    """Light‑weight manager: spawns a short‑lived worker only when capacity allows."""
    from erieiron_common.message_queue.resource_manager import get_system_capacity

    while True:
        try:
            if _should_start_worker(thread_manager, max_percent_capacity_consumed):
                worker = ManagedThread(
                    thread_locals=thread_manager.thread_locals,
                    on_init=thread_manager.func_on_init,
                    on_destroy=thread_manager.func_on_destroy,
                    on_loop=thread_manager.func_on_loop
                )
                worker.start()
        except Exception as e:
            logging.exception(e)
        finally:
            try:
                if thread_manager.func_on_thread_management_complete:
                    system_capacity, explanation = get_system_capacity(max_percent_capacity_consumed)
                    thread_manager.func_on_thread_management_complete(
                        [],
                        system_capacity,
                        explanation
                    )
            except Exception as e:
                logging.exception(e)
            time.sleep(THREAD_MANAGER_LOOP_SEC)


class ManagedThread(threading.Thread):
    def __init__(
            self,
            thread_locals,
            on_loop=None,
            on_init=None,
            on_destroy=None
    ):
        os.environ["TREE_SITTER_SKIP_VENDOR"] = "1"
        super().__init__(
            target=self.target,
            name=f"managed_thread-{uuid.uuid4()}",
            daemon=True
        )

        self.thread_locals = thread_locals
        self.on_loop = on_loop
        self.on_init = on_init
        self.on_destroy = on_destroy

    def target(self):
        from erieiron_common import cache

        # copy thread‑locals
        for k, v in self.thread_locals.items():
            cache.tl_set(k, v)

        close_old_connections()
        with contextlib.closing(connections['default']) as db_conn:
            try:
                if self.on_init:
                    self.on_init()
                if self.on_loop:
                    self.on_loop(db_conn)  # single‑shot
            finally:
                if self.on_destroy:
                    try:
                        self.on_destroy()
                    except Exception as e:
                        logging.exception(e)


def pubsub_workflow(function):
    @wraps(function)
    def wrap(pubsub_manager: 'PubSubManager'):
        return function(pubsub_manager)

    return wrap


class PubSubManager:
    _internal_instance: 'PubSubManager' = None

    def __init__(self):
        self.thread_manager: ThreadManager = None
        self.handler_instance_id = common.get_machine_name()
        self.process_id = None
        self.lock = threading.Lock()
        self.exclusive_message_types: List[PubSubMessageType] = []
        self.exclude_message_types: List[PubSubMessageType] = []
        self.environment_id: uuid.UUID = self.get_env().id

    @classmethod
    def set_instance(cls, instance: 'PubSubManager') -> 'PubSubManager':
        # should only be use internally to this class or for testing
        cls._internal_instance = instance
        return cls._internal_instance

    @classmethod
    def get_instance(cls) -> 'PubSubManager':
        if cls._internal_instance:
            return cls._internal_instance
        else:
            i = PubSubManager()
            return cls.set_instance(i)

    def __str__(self):
        if self.thread_manager:
            return f"""
            Instance:    {self.get_handler()}
            Process:     {self.get_process()}
            Max Threads: {self.thread_manager.func_get_max_threads()}
            """
        else:
            return f"{self.__class__.__name__} not yet initialized"

    def initialize(
            self,
            env: PubSubEnvironment,
            handler: PubSubHanderInstance = None,
            process: PubSubHanderInstanceProcess = None,
            force=False
    ):
        exclusive = []
        exclude = []
        for mt in common.safe_split(settings_common.MESSAGE_TYPES):
            if mt.startswith("-"):
                exclude.append(mt[1:])
            else:
                exclusive.append(mt)

        self.exclusive_message_types = PubSubMessageType.to_list(exclusive)
        self.exclude_message_types = PubSubMessageType.to_list(exclude)

        if isinstance(env, PubSubEnvironment):
            self.environment_id = env.id
        else:
            self.environment_id = env

        if PubSubMessagePriority.HIGH.eq(self.get_exclusive_priority()):
            max_percent_capacity_consumed = 99
        else:
            max_percent_capacity_consumed = 70

        if handler and process:
            if not (force or settings_common.START_MESSAGE_QUEUE_PROCESSOR):
                raise Exception("attempting to initialize pubsub manager, but pubsub not supported per settings")

            self.handler_instance_id = handler.id
            self.process_id = process.id

            if self.thread_manager is None:
                self.thread_manager = ThreadManager(
                    max_percent_capacity_consumed=max_percent_capacity_consumed,
                    func_on_loop=self.do_work,
                    func_on_init=self.on_thread_create,
                    func_on_destroy=self.on_thread_destroy,
                    func_get_min_threads=self.get_min_threads,
                    func_get_max_threads=self.get_max_threads,
                    func_get_current_threads=self.get_current_threads,
                    func_needs_threads=self.needs_threads,
                    func_on_thread_management_complete=self.on_thread_management_complete
                )

            common.log_debug(f"PUB SUB initializing {self.get_handler()}")

            for workflow_init_method in get_methods_with_decorator("@pubsub_workflow"):
                common.log_debug(f"initing pubsub workflow: {workflow_init_method.__module__}.{workflow_init_method.__name__}()")
                workflow_init_method(self)
        else:
            self.handler_instance_id = None
            self.process_id = None

        return self

    @staticmethod
    def get_env(run_isolated=False) -> PubSubEnvironment:
        settings_env = settings_common.MESSAGE_QUEUE_ENV or common.get_machine_name()

        if run_isolated:
            cache = common.get_local_server_cache()
            env = cache.get(
                "isolated_env_name",  # if multiple processors running on same box, use same name
                f"{settings_env}_isolated_{uuid.uuid4()}"
            )
            cache.set("isolated_env_name", env)
        else:
            env = settings_env

        return PubSubEnvironment.objects.get_or_create(id=env)[0]

    def supports_cuda_msgs(self) -> bool:
        return settings_common.DEBUG or ComputeDevice.CUDA.eq(self.compute_device)

    @cached_property
    def compute_device(self) -> ComputeDevice | None:
        h = self.get_handler()
        return ComputeDevice(h.compute_device) if h.compute_device else None

    def get_environment(self) -> PubSubEnvironment:
        return PubSubEnvironment.objects.filter(id=self.environment_id).first()

    def get_handler(self) -> PubSubHanderInstance:
        if not self.handler_instance_id:
            raise Exception("pubsub manager not initialized yet")

        return PubSubHanderInstance.objects.get_or_create(
            id=self.handler_instance_id,
            defaults={
                "compute_device": ComputeDevice.CPU # gpu_utils.get_compute_device()
            }
        )[0]

    def get_process(self) -> PubSubHanderInstanceProcess:
        return PubSubHanderInstanceProcess.objects.get(id=self.process_id)

    def get_messages_for_host(self, status=PubSubMessageStatus) -> QuerySet['PubSubMessage']:
        return self.get_handler().get_messages_for_host(status=status)

    def has_pending_messages(self, namespace, message_types=None, excluding_message=None) -> bool:
        return PubSubMessage.has_pending_messages(
            namespace=namespace,
            env=self.environment_id,
            message_type=message_types,
            excluding_message=excluding_message
        )

    def has_pending_messages_for_id(self, namespace_id, message_types=None, excluding_message=None) -> bool:
        return PubSubMessage.has_pending_messages_for_id(
            namespace_id=namespace_id,
            env=self.environment_id,
            message_types=message_types,
            excluding_message=excluding_message
        )

    def get_message_to_process(self, priority=None) -> 'PubSubMessage | None':
        with self.lock:
            with transaction.atomic():
                q = PubSubMessage.objects.select_for_update(
                    skip_locked=True
                ).filter(
                    env=self.environment_id,
                    status=PubSubMessageStatus.PENDING
                )

                if priority:
                    q = q.filter(priority=priority)

                if self.exclusive_message_types:
                    q = q.filter(
                        message_type__in=PubSubMessageType.to_value_list(self.exclusive_message_types)
                    )

                exclude_message_types = self.get_overlimit_message_types()
                exclude_message_types += common.ensure_list(self.exclude_message_types)

                if not self.supports_cuda_msgs():
                    exclude_message_types += PubSubMessageType.get_cuda_only_message_types()

                if len(exclude_message_types) > 0:
                    q = q.exclude(
                        message_type__in=PubSubMessageType.to_value_list(exclude_message_types)
                    )

                message: PubSubMessage = q.order_by('created_at').first()

                if message is None:
                    return

                PubSubMessage.objects.filter(id=message.id).update(
                    status=PubSubMessageStatus.PROCESSING,
                    handler_instance_id=self.handler_instance_id,
                    start_time=get_now(),
                    updated_at=get_now()
                )

                # mirror DB state on the in-memory instance to avoid a second query
                message.status = PubSubMessageStatus.PROCESSING
                message.handler_instance_id = self.handler_instance_id
                message.start_time = get_now()
                message.updated_at = message.start_time

        return message

    def has_active_work(self) -> int:
        handler = PubSubHanderInstance.objects.get(id=self.handler_instance_id)
        in_prog_messages, _ = handler.get_inprogress_messages()
        return len(in_prog_messages) > 0

    def get_current_threadcount(self) -> int:
        return PubSubHanderInstance.objects.get(id=self.handler_instance_id).get_current_threadcount()

    def is_drain_requested(self) -> bool:
        try:
            return self.get_handler().is_drain_requested() or self.get_process().is_drain_requested()
        except PubSubHanderInstanceProcess.DoesNotExist:
            logging.info("process not found during is_drain_requested")
            return True
        except PubSubHanderInstance.DoesNotExist:
            logging.info("instance not found during is_drain_requested")
            return True

    def reset(self):
        self.get_handler().pubsubhanderinstanceprocess_set.exclude(
            handler_instance_id=self.handler_instance_id,
            pid=os.getpid()
        ).delete()

        return self

    def mark_available(self):
        self.get_handler().set_status(PubSubHandlerInstanceStatus.AVAILABLE)
        self.get_process().set_status(PubSubHandlerInstanceStatus.AVAILABLE)

        return self

    def print_pending(self):
        failed_messages = PubSubMessage.get_failed_messages(env=self.environment_id)
        self.print_messages("failed", failed_messages)

        pending_messages = PubSubMessage.get_pending_messages(env=self.environment_id, limit=1000)
        self.print_messages("unfinished", pending_messages)

        return pending_messages, failed_messages

    @staticmethod
    def print_messages(label: str, pubsub_messages: QuerySet['PubSubMessage']):
        if not pubsub_messages.exists():
            return

        msg_type_dict = defaultdict(list)
        [msg_type_dict[PubSubMessageStatus(m.status)].append(m) for m in pubsub_messages]

        order_of_reporting = [
            PubSubMessageStatus.PROCESSING,
            PubSubMessageStatus.PENDING,
            PubSubMessageStatus.PROCESSED,
            PubSubMessageStatus.FAILED,
            PubSubMessageStatus.NO_CONSUMER,
        ]

        if PubSubManager.get_instance().is_drain_requested():
            drainging_msg = " (draining) "
        else:
            drainging_msg = ""

        banner_msg = f"---- {len(pubsub_messages)} {label.upper()} MESSAGES{drainging_msg}------------------------------------------------------------------------"
        msgs = ["", banner_msg]
        for status in order_of_reporting:
            for pm in sorted(msg_type_dict[status], key=lambda m: m.created_at):
                try:
                    msgs.append(pm)
                except:
                    msgs.append(f"exception with {pm.name}")
        msgs.append("-" * len(banner_msg))
        msgs.append("")
        print("\n".join(["\t" + str(m) for m in msgs]))

    @staticmethod
    def publish_id(
            message_type: PubSubMessageType,
            msg_id=None,
            priority: PubSubMessagePriority = PubSubMessagePriority.NORMAL,
            batch_idx=None
    ) -> PubSubMessage:
        instance = PubSubManager.get_instance()
        return instance.publish(
            message_type=message_type,
            namespace_context=msg_id or uuid.uuid4(),
            payload=msg_id or {},
            priority=priority,
            batch_idx=batch_idx
        )

    @staticmethod
    def publish(
            message_type: PubSubMessageType,
            namespace_context=None,
            payload=None,
            priority: PubSubMessagePriority = PubSubMessagePriority.NORMAL,
            batch_idx=None
    ) -> PubSubMessage:
        instance = PubSubManager.get_instance()

        if payload is None:
            payload = {}

        try:
            namespace = message_type.get_namespace(namespace_context)
        except:
            namespace = message_type.value

        message = PubSubMessage.create(
            env=instance.environment_id,
            message_type=message_type,
            priority=priority,
            namespace=namespace,
            payload=payload,
            batch_idx=batch_idx
        )

        instance.log_status("1. PUBLISHED", message)

        return message

    def on(
            self,
            message_type: PubSubMessageType,
            handler_method,
            completed_message_type: PubSubMessageType = None,
            error_handler_method=None

    ) -> 'PubSubManager':
        for mt in common.ensure_list(message_type):
            subscribers[mt].add(
                (handler_method, error_handler_method, completed_message_type)
            )

        return self

    def on_thread_management_complete(
            self,
            current_threads,
            system_capacity: SystemCapacity,
            explanation: str
    ):
        """
        Called when thread management completes a cycle.
        """
        try:
            self.get_process().ping()
        except:
            pass

    def on_thread_create(self):
        common.log_debug(f"PUB SUB:  creating worker thread {common.get_current_thread_name()}")

        try:
            self.get_process().update_thread(
                common.get_current_thread_name(),
                "init"
            )
        except PubSubHanderInstance.DoesNotExist as e:
            logging.info(f"failed to update thread for instance {e}")
            raise ThreadShutdownException(e)

    def on_thread_destroy(self):
        common.log_debug(f"PUB SUB:  destroying worker thread {common.get_current_thread_name()}")
        try:
            process = self.get_process()
            process.remove_thread(common.get_current_thread_name())
        except PubSubHanderInstanceProcess.DoesNotExist:
            common.log_debug(f"PUB SUB: process {self.process_id} no longer exists")

    def do_work(self, thread_conn):
        if self.is_drain_requested():
            return

        message = self.get_message_to_process(self.get_exclusive_priority())
        if not message:
            time.sleep(SLEEP_BACKOFF_NO_WORK)  # back‑off when queue empty
            return

        self.process_message(message)

    def get_exclusive_priority(self):
        try:
            process = self.get_process()
            exclusive_priority = process.exclusive_priority
            return exclusive_priority
        except:
            return

    def get_overlimit_message_types(self, excluding_message: PubSubMessage = None):
        ingestion_limits = self.get_ingestion_limits()

        q = PubSubMessage.objects.filter(
            env=self.environment_id,
            handler_instance_id=self.handler_instance_id,
            message_type__in=ingestion_limits.keys(),
            status=PubSubMessageStatus.PROCESSING
        )

        if excluding_message:
            q = q.exclude(id=excluding_message.id)

        dict_messagetype_counts = Counter(
            PubSubMessageType(msg.message_type) for msg in q
        )

        exclude_message_types = []
        for message_type, limit in ingestion_limits.items():
            message_type_count = common.get(dict_messagetype_counts, message_type, 0)
            if message_type_count >= limit:
                exclude_message_types.append(message_type)

        return exclude_message_types

    def process_message(self, message: 'PubSubMessage'):
        message_type = PubSubMessageType(message.message_type)

        subscriber_methods = subscribers[message_type]
        if len(subscriber_methods) == 0:
            logging.error(f"No consumer for {message_type}")

            PubSubMessage.mark_finished(
                message.id,
                PubSubMessageStatus.NO_CONSUMER,
                self.handler_instance_id
            )

            return

        current_thread_name = common.get_current_thread_name()
        try:
            self.get_process().update_thread(
                current_thread_name,
                message.get_job_name(),
                message
            )

            subscriber_method_signatures = None
            try:
                subscriber_method_signatures = ", ".join([
                    f"{subscriber_method[0].__module__}.{subscriber_method[0].__qualname__}()"
                    for subscriber_method in subscriber_methods if subscriber_method[0]
                ])
            except Exception as e:
                subscriber_method_signatures = str(e)

            millis_to_pickup = (get_now() - message.updated_at).microseconds // 1000
            common.log_info(f"PUB SUB:  picked up '{message.message_type}' {message.id} after {millis_to_pickup}ms with {subscriber_method_signatures};")

            PubSubMessage.mark_processing(message.id, self.handler_instance_id)
            close_old_connections()

            for handler_method, error_handler_method, completed_message_type in subscriber_methods:
                try:
                    if handler_method:
                        # hey let's stop messing around and actually do some work around here
                        ret_val = self.execute_message_handler(
                            handler_method,
                            message
                        )
                    else:
                        # no-op handler
                        ret_val = None

                    PubSubMessage.mark_finished(
                        message.id,
                        PubSubMessageStatus.PROCESSED,
                        self.handler_instance_id
                    )

                    if completed_message_type:
                        if common.is_list_like(ret_val):
                            for rt in common.ensure_list(ret_val):
                                PubSubManager.publish(
                                    completed_message_type,
                                    namespace_context=message.namespace,
                                    payload=rt or message.payload,
                                    priority=message.priority
                                )
                        else:
                            PubSubManager.publish(
                                completed_message_type,
                                namespace_context=message.namespace,
                                payload=ret_val or message.payload,
                                priority=message.priority
                            )

                    common.log_info(f"PUB SUB:  handled '{message.message_type}' {message.id} with {subscriber_method_signatures};")

                    # no errors, great let's break
                    break
                except ObjectDoesNotExist as e:
                    # some related object no longer exists.  mark obsolete
                    PubSubMessage.mark_finished(
                        message.id,
                        PubSubMessageStatus.OBSOLETE,
                        self.handler_instance_id,
                        str(e)
                    )
                except Exception as e:
                    retry_count = common.default(message.retry_count, 0) + 1
                    if retry_count > MESSAGE_HANDLER_MAX_RETRIES:
                        raise e
                    else:
                        with transaction.atomic():
                            # back on the queue for retry
                            PubSubMessage.objects.filter(id=message.pk).update(
                                error_message=str(e),
                                status=PubSubMessageStatus.PENDING,
                                retry_count=retry_count
                            )
        except Exception as e:
            PubSubMessage.mark_finished(
                message.id,
                PubSubMessageStatus.FAILED,
                self.handler_instance_id,
                str(e)
            )

            for _, error_handler_method, _ in subscriber_methods:
                if error_handler_method:
                    try:
                        error_handler_method(
                            message.payload,
                            message,
                            e
                        )
                    except Exception as e2:
                        logging.exception(e2)

            common.log_info(f"PUB SUB:  erred", message)
            logging.exception(e)
        finally:
            try:
                self.get_process().remove_thread(current_thread_name)
            except Exception as e:
                logging.exception(e)

    def execute_message_handler(self, handler_method, message):
        sig = inspect.signature(handler_method)
        param_count = len(sig.parameters)

        self.log_status("3. HANDLING", message)
        if param_count == 0:
            ret_val = handler_method()
        elif param_count == 1:
            ret_val = handler_method(message.payload)
        elif param_count == 2:
            ret_val = handler_method(message.payload, message)
        else:
            raise ValueError(f"invalid method signature {handler_method}: {param_count} parms")

        self.log_status("4. DONE HANDLING", message)

        return ret_val

    def log_status(self, prefix, message: PubSubMessage):
        thread_name = common.get_current_thread_name()
        msg = f"PUB SUB {prefix} {message.message_type} thread={thread_name} env={message.env}"
        common.log_debug(msg)

    def get_current_threads(self):
        return self.get_process().to_dict()['thread_datas']

    def needs_threads(self):
        threshold_time = timezone.now() - timedelta(seconds=10)

        return PubSubMessage.fetch(
            status=PubSubMessageStatus.PENDING,
            env=self.environment_id
        ).exists()

    def get_min_threads(self):
        if self.is_drain_requested():
            return 0
        else:
            return 1

    def get_max_threads(self):
        if self.is_drain_requested():
            return 0
        else:
            return self.get_handler().threads_per_process

    def get_ingestion_limits(self) -> Dict[PubSubMessageType, int]:
        limits = {}

        job_limits_def = common.default_str(self.get_handler().job_limits_def)

        for entry in common.safe_split(job_limits_def):
            parts = common.safe_split(entry, strip=True, delimeter=":")
            try:
                limits[PubSubMessageType(parts[0])] = int(parts[1])
            except Exception as e:
                logging.exception(e)

        return limits

    def is_cuda_running(self):
        return PubSubHanderInstance.objects.filter(
            environment_id=self.environment_id,
            instance_status=PubSubHandlerInstanceStatus.AVAILABLE,
            compute_device=ComputeDevice.CUDA
        ).exists()

    @classmethod
    def get_hung_message_ids(cls, env, multiplier=3) -> list[uuid.UUID]:
        messages = PubSubMessage.objects.filter(
            env=env,
            status=PubSubMessageStatus.PROCESSING,
            start_time__isnull=False
        )

        current_time = get_now()
        p90_thresholds = cls.get_p90_thresholds()
        long_runners = []
        for msg in messages:
            p90_seconds = p90_thresholds.get(msg.message_type)
            if p90_seconds is None:
                continue  # skip message types not in the JSON

            threshold = timedelta(seconds=p90_seconds * multiplier)
            elapsed = current_time - msg.start_time

            if elapsed > threshold:
                long_runners.append(msg.id)

        return long_runners

    @classmethod
    @lru_cache
    def get_p90_thresholds(cls):
        json_path = Path(__file__).parent / "message_type_90thtime.json"
        try:
            with open(json_path, "r") as f:
                return json.load(f)
        except FileNotFoundError:
            logging.warning("p90 threshold file %s not found – using empty dict", json_path)
            return {}

    @classmethod
    def noop(cls):
        pass


def init_pubsub_from_cmd_options(options=None):
    if options is None:
        options = {}

    instance_id = common.default_str(options.get("instance_id"), common.get_machine_name())

    env = options.get('env')
    if env:
        env = PubSubEnvironment.objects.get_or_create(id=env)[0]
    else:
        env = PubSubManager.get_env(options.get("run_isolated"))

    handler = PubSubHanderInstance.get_or_create(env, instance_id)

    proc = PubSubHanderInstanceProcess.get_or_create(
        handler_instance_id=handler.id,
        pid=os.getpid(),
        exclusive_priority=options.get("exclusive_priority")
    )

    pubsub_manager = PubSubManager.get_instance().initialize(
        env=env,
        handler=handler,
        process=proc,
        force=True
    )

    if options.get("reset_host"):
        pubsub_manager.reset()

    pubsub_manager.mark_available()

    return pubsub_manager
