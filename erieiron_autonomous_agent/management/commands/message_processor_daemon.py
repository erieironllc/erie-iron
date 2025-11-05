import faulthandler
import os
import pprint
import time
import warnings
from datetime import timedelta
from datetime import timezone

import openai
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

import settings
from erieiron_autonomous_agent.models import RunningProcess
from erieiron_common import common
from erieiron_common.common import parse_bool
from erieiron_common.enums import PubSubHandlerInstanceStatus, PubSubMessagePriority, PubSubMessageStatus, PubSubMessageType
from erieiron_common.message_queue.pubsub_manager import pubsub_workflow, PubSubManager
from erieiron_common.models import PubSubHanderInstanceProcess, PubSubHanderInstance, PubSubMessage


os.environ["TREE_SITTER_SKIP_VENDOR"] = "1"
warnings.filterwarnings("ignore", category=FutureWarning, module="tree_sitter")

@pubsub_workflow
def add_noop_handlers(pubsub_manager: PubSubManager):
    # this is here to suppress the 'No consumer' messages
    pubsub_manager.on(
        [
            PubSubMessageType.EVERY_WEEK,
            PubSubMessageType.EVERY_DAY,
            PubSubMessageType.EVERY_HOUR,
            PubSubMessageType.EVERY_MINUTE
        ],
        PubSubManager.noop
    )


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument(
            '--suppress_timing_messages',
            type=parse_bool,
            required=False,
            default=False
        )
        
        parser.add_argument(
            '--reset_host',
            help='start with a fresh instance metadata',
            type=parse_bool,
            required=False,
            default=False
        )
        
        parser.add_argument(
            '--retry_failed',
            type=parse_bool,
            required=False,
            default=False
        )
        
        parser.add_argument(
            '--instance_id',
            help='name of the instance.  defaults to machine name',
            required=False
        )
        
        parser.add_argument(
            '--max_threads',
            required=False,
            default=None
        )
        
        parser.add_argument(
            '--env',
            help='the environment to pull messages from.  defaults to value in settings',
            required=False,
            default=False
        )
        
        parser.add_argument(
            '--exclusive_priority',
            help='if supplied, this processor will only fetch messages with the specified priority',
            choices=[p.value for p in PubSubMessagePriority],
            required=False,
            default=None
        )
        
        parser.add_argument(
            '--run_isolated',
            help='run the message processor in an isolated environment - for testing purposes.  in prod this is False',
            type=parse_bool,
            required=False,
            default=False
        )
        
        parser.add_argument(
            '--kill_on_drain',
            help='kill the process upon drain completion',
            type=parse_bool,
            required=False,
            default=False
        )
        
        parser.add_argument(
            '--debug_output',
            help='print messages debug info',
            type=parse_bool,
            required=False,
            default=False
        )
    
    def handle(self, *args, **options):
        pprint.pprint(settings.DATABASES)
        from erieiron_common.llm_apis import openai_chat_api
        openai.api_key = openai_chat_api.get_api_key()
        
        if options.get("retry_failed"):
            PubSubMessage.objects.filter(status__in=[PubSubMessageStatus.FAILED, PubSubMessageStatus.PROCESSING, PubSubMessageStatus.NO_CONSUMER]).update(
                status=PubSubMessageStatus.PENDING
            )
        
        faulthandler.enable()
        
        print_debug_info = options.get('debug_output')
        kill_on_drain = options.get('kill_on_drain')
        run_isolated = options.get('run_isolated')
        reset_host = options.get('reset_host')
        exclusive_priority = options.get('exclusive_priority')
        
        from erieiron_common.message_queue.pubsub_manager import init_pubsub_from_cmd_options
        pubsub_manager = init_pubsub_from_cmd_options(options)
        
        handler = pubsub_manager.get_handler()
        process = pubsub_manager.get_process()
        process_id = process.id
        
        env = handler.environment
        log_prefix = f"{handler.id} ({os.getpid()})"
        log_suffix = ""  # f"https://localhost/admin/message_handler/{handler.id}"
        common.log_info(f"""
        
        ----------------------------------------------------------------------------
        The WORLD FAMOUS Erie Iron Message Processor is started and open for business
         
        I'm an OS process (pid {os.getpid()}) that has zero or more worker threads 
        My job is as follows:
        a) Manage my threadcount so I have enough threads to do work, but not too many 
           as to overload the system (checking cpu/memory/db conns/etc)
        b) My worker threads pick up messages off the message queue and do the work
        c) Keep the peace
        
        {log_suffix}

        Env            {env}
        Instance       {handler}
        Process        {process}
        Compute Device {handler.compute_device}
        Max Threads    {handler.threads_per_process}
        ----------------------------------------------------------------------------


        """)
        
        try:
            os.setsid()
        except:
            pass
        
        try:
            idx = 0
            while True:
                process = PubSubHanderInstanceProcess.objects.filter(id=process_id).first()
                
                if not common.parse_bool(options.get("suppress_timing_messages")):
                    publish_timing_messages()
                
                # Check for timed-out running processes
                check_timed_out_processes()
                
                if not process:
                    common.log_info(f"""
                    ----------------------------------------------------------------------
                    PubSubHanderInstanceProcess id={process_id} not longer exists.  killing the pid.  goodbye from {log_prefix}
                    https://www.youtube.com/watch?v=r4HNcX9uD-M&t=133s
                    ----------------------------------------------------------------------
                    """)
                    exit(0)
                
                if not PubSubHanderInstance.objects.filter(id=handler.id).exists():
                    common.log_info(f"""
                    ----------------------------------------------------------------------
                    PubSubHanderInstance id={handler.id} not longer exists.  killing this pid {os.getpid()}.  goodbye from {log_prefix}
                    https://www.youtube.com/watch?v=r4HNcX9uD-M&t=133s
                    ----------------------------------------------------------------------
                    """)
                    exit(0)
                
                if process.is_drain_requested():
                    processing_messages = list(process.get_inprogress_messages())
                    if PubSubHandlerInstanceStatus.KILL_REQUESTED.eq(process.process_status):
                        pubsub_manager.reset()
                        common.log_info(f"""
                        ----------------------------------------------------------------------
                        somebody killed me, so respecting that and shutting down now.  goodbye from {log_prefix}
                        https://www.youtube.com/watch?v=r4HNcX9uD-M&t=133s
                        ----------------------------------------------------------------------
                        """)
                        exit(0)
                    elif idx % 10 == 0 and len(processing_messages) > 0:
                        log(f'message_queue_processor draining for env {env} ', log_prefix, log_suffix)
                        pubsub_manager.print_messages("waiting on", processing_messages)
                    elif len(processing_messages) == 0 and kill_on_drain:
                        pubsub_manager.reset()
                        common.log_info(f"""
                        ----------------------------------------------------------------------
                        all messages finished, shutting down now.  goodbye from {log_prefix}
                        https://www.youtube.com/watch?v=r4HNcX9uD-M&t=133s
                        ----------------------------------------------------------------------
                        """)
                        exit(0)
                    else:
                        log(f'message_queue_processor drained for env {env} ', log_prefix, log_suffix)
                else:
                    process.ping()
                    
                    if idx % 10 == 0 and print_debug_info:
                        # log(f'message_queue_processor running for env {env} ', log_prefix, log_suffix)
                        pubsub_manager.print_pending()
                
                idx += 1
                time.sleep(1)
        
        except KeyboardInterrupt:
            log(f'message_queue_processor stopped for env {env} ', log_prefix, log_suffix)
            exit(0)


def _create_timing_message_safe(
        message_type: PubSubMessageType,
        namespace,
        environment_id,
        success_message
):
    """
    Creates a timing message with proper database-level locking to prevent race conditions.
    Uses a lock table approach to ensure only one daemon per environment can create timing messages.
    """
    try:
        with transaction.atomic():
            # Use row-level locking on handler instance to coordinate timing message creation
            # This ensures only one daemon per environment can create timing messages at a time
            handler_instance = PubSubHanderInstance.objects.select_for_update(
                skip_locked=True
            ).filter(
                environment=environment_id
            ).first()
            
            if handler_instance:
                existing_message = PubSubMessage.objects.filter(
                    message_type=message_type.value,
                    namespace=namespace,
                    env=environment_id
                ).first()
                
                if not existing_message:
                    message = PubSubMessage.objects.create(
                        message_type=message_type.value,
                        namespace=namespace,
                        env=environment_id,
                        priority=PubSubMessagePriority.NORMAL.value,
                        status=PubSubMessageStatus.PENDING.value,
                        payload={}
                    )
    
    except Exception as e:
        # Log unexpected errors but don't fail
        common.log_info(f"Error creating timing message {message_type}/{namespace}: {e}")


def publish_timing_messages(enable_every_minute=True):
    now = common.get_now()
    minute_namespace = f"timing_minute_{now.strftime('%Y%m%d_%H%M')}"
    hour_namespace = f"timing_hour_{now.strftime('%Y%m%d_%H')}"
    day_namespace = f"timing_day_{now.strftime('%Y%m%d')}"
    week_namespace = f"timing_week_{now.strftime('%Y%U')}"
    
    environment_id = PubSubManager.get_instance().environment_id
    
    if enable_every_minute:
        _create_timing_message_safe(
            PubSubMessageType.EVERY_MINUTE,
            minute_namespace,
            environment_id,
            f"Published EVERY_MINUTE message for {minute_namespace}"
        )
    
    _create_timing_message_safe(
        PubSubMessageType.EVERY_HOUR,
        hour_namespace,
        environment_id,
        f"Published EVERY_HOUR message for {hour_namespace}"
    )
    
    _create_timing_message_safe(
        PubSubMessageType.EVERY_DAY,
        day_namespace,
        environment_id,
        f"Published EVERY_DAY message for {day_namespace}"
    )
    
    _create_timing_message_safe(
        PubSubMessageType.EVERY_WEEK,
        week_namespace,
        environment_id,
        f"Published EVERY_WEEK message for {week_namespace}"
    )


def check_timed_out_processes():
    """
    Check for running processes that have exceeded their task's timeout and kill them.
    """
    
    # Get all running processes that have an associated task with a timeout
    running_processes = RunningProcess.objects.filter(
        is_running=True,
        task_execution__task__timeout_seconds__isnull=False
    ).select_related('task_execution__task')
    
    for process in running_processes:
        task = process.task_execution.task
        timeout_seconds = task.timeout_seconds
        
        if timeout_seconds and timeout_seconds > 0:
            # Calculate how long the process has been running
            runtime = timezone.now() - process.started_at
            timeout_threshold = timedelta(seconds=timeout_seconds)
            
            if runtime > timeout_threshold:
                try:
                    process.kill_process()
                    
                    common.log_info(
                        f"Killed timed-out process {process.id} for task {task.id} "
                        f"(timeout: {timeout_seconds}s, runtime: {int(runtime.total_seconds())}s)"
                    )
                except Exception as e:
                    common.log_error(f"Failed to kill timed-out process {process.id}: {e}")


def log(msg, prefix, suffix):
    common.log_info(f'{prefix} {msg} {suffix} ')
