import faulthandler
import os
import time
from datetime import datetime, timezone

import openai
from django.core.management.base import BaseCommand

from erieiron_common import common
from erieiron_common.common import parse_bool
from erieiron_common.enums import PubSubHandlerInstanceStatus, PubSubMessagePriority, PubSubMessageStatus, PubSubMessageType
from erieiron_common.message_queue.pubsub_manager import pubsub_workflow, PubSubManager
from erieiron_common.models import PubSubHanderInstanceProcess, PubSubHanderInstance, PubSubMessage


@pubsub_workflow
def add_noop_handlers(pubsub_manager: PubSubManager):
    # this is here to suppress the 'No consumer' messages
    pubsub_manager.on(
        [PubSubMessageType.EVERY_HOUR, PubSubMessageType.EVERY_MINUTE],
        PubSubManager.noop
    )


class Command(BaseCommand):
    def add_arguments(self, parser):
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
        from erieiron_common.llm_apis import openai_chat_api
        openai.api_key = openai_chat_api.get_api_key()

        if options.get("retry_failed"):
            PubSubMessage.objects.filter(status__in=[PubSubMessageStatus.FAILED, PubSubMessageStatus.PROCESSING, PubSubMessageStatus.NO_CONSUMER]).update(
                status=PubSubMessageStatus.PENDING
            )

        faulthandler.enable()

        print_debug_info = True  # options.get('debug_output')
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

                publish_timing_messages()

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
                        log(f'message_queue_processor running for env {env} ', log_prefix, log_suffix)
                        pubsub_manager.print_pending()

                idx += 1
                time.sleep(1)

        except KeyboardInterrupt:
            log(f'message_queue_processor stopped for env {env} ', log_prefix, log_suffix)
            exit(0)


def publish_timing_messages(enable_every_minute=False):
    """
    Publishes timing messages at intervals with distributed coordination.
    Robust to daemon delays/restarts - checks for missing messages in current period.
    Only one daemon across all hosts will publish each timing message.
    """
    from erieiron_common.message_queue.pubsub_manager import PubSubManager
    from django.db import transaction

    now = datetime.now(timezone.utc)

    # EVERY_MINUTE: check if current minute's message exists
    minute_namespace = f"timing_minute_{now.strftime('%Y%m%d_%H%M')}"
    if enable_every_minute and not PubSubMessage.objects.filter(
            message_type=PubSubMessageType.EVERY_MINUTE.value,
            namespace=minute_namespace
    ).exists():
        try:
            with transaction.atomic():
                message, created = PubSubMessage.objects.get_or_create(
                    message_type=PubSubMessageType.EVERY_MINUTE.value,
                    namespace=minute_namespace,
                    defaults={
                        'env': PubSubManager.get_instance().environment_id,
                        'priority': PubSubMessagePriority.NORMAL.value,
                        'status': PubSubMessageStatus.PENDING.value,
                        'payload': {}
                    }
                )
                if created:
                    common.log_info(f"Published EVERY_MINUTE message for {minute_namespace}")
        except Exception as e:
            # Another daemon already published this timing message
            pass

    # EVERY_HOUR: check if current hour's message exists
    hour_namespace = f"timing_hour_{now.strftime('%Y%m%d_%H')}"
    if not PubSubMessage.objects.filter(
            message_type=PubSubMessageType.EVERY_HOUR.value,
            namespace=hour_namespace
    ).exists():
        try:
            with transaction.atomic():
                message, created = PubSubMessage.objects.get_or_create(
                    message_type=PubSubMessageType.EVERY_HOUR.value,
                    namespace=hour_namespace,
                    defaults={
                        'env': PubSubManager.get_instance().environment_id,
                        'priority': PubSubMessagePriority.NORMAL.value,
                        'status': PubSubMessageStatus.PENDING.value,
                        'payload': {}
                    }
                )
                if created:
                    common.log_info(f"Published EVERY_HOUR message for {hour_namespace}")
        except Exception as e:
            # Another daemon already published this timing message
            pass

    # EVERY_DAY: check if current day's message exists
    day_namespace = f"timing_day_{now.strftime('%Y%m%d')}"
    if not PubSubMessage.objects.filter(
            message_type=PubSubMessageType.EVERY_DAY.value,
            namespace=day_namespace
    ).exists():
        try:
            with transaction.atomic():
                message, created = PubSubMessage.objects.get_or_create(
                    message_type=PubSubMessageType.EVERY_DAY.value,
                    namespace=day_namespace,
                    defaults={
                        'env': PubSubManager.get_instance().environment_id,
                        'priority': PubSubMessagePriority.NORMAL.value,
                        'status': PubSubMessageStatus.PENDING.value,
                        'payload': {}
                    }
                )
                if created:
                    common.log_info(f"Published EVERY_DAY message for {day_namespace}")
        except Exception as e:
            # Another daemon already published this timing message
            pass

    # EVERY_WEEK: check if current week's message exists (Monday-based weeks)
    week_namespace = f"timing_week_{now.strftime('%Y%U')}"
    if not PubSubMessage.objects.filter(
            message_type=PubSubMessageType.EVERY_WEEK.value,
            namespace=week_namespace
    ).exists():
        try:
            with transaction.atomic():
                message, created = PubSubMessage.objects.get_or_create(
                    message_type=PubSubMessageType.EVERY_WEEK.value,
                    namespace=week_namespace,
                    defaults={
                        'env': PubSubManager.get_instance().environment_id,
                        'priority': PubSubMessagePriority.NORMAL.value,
                        'status': PubSubMessageStatus.PENDING.value,
                        'payload': {}
                    }
                )
                if created:
                    common.log_info(f"Published EVERY_WEEK message for {week_namespace}")
        except Exception as e:
            # Another daemon already published this timing message
            pass


def log(msg, prefix, suffix):
    common.log_info(f'{prefix} {msg} {suffix} ')
