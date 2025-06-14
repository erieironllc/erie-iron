import faulthandler
import os
import time

import openai
from django.core.management.base import BaseCommand

from erieiron_common import common
from erieiron_common.common import parse_bool
from erieiron_common.enums import PubSubHandlerInstanceStatus, PubSubMessagePriority, PubSubMessageStatus
from erieiron_common.models import PubSubHanderInstanceProcess, PubSubHanderInstance, PubSubMessage


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

                # publish_timing_messages()

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

                    if False and idx % 10 == 0 and print_debug_info:
                        log(f'message_queue_processor running for env {env} ', log_prefix, log_suffix)
                        pubsub_manager.print_pending()

                idx += 1
                time.sleep(1)

        except KeyboardInterrupt:
            log(f'message_queue_processor stopped for env {env} ', log_prefix, log_suffix)
            exit(0)


# def publish_timing_messages():
# one_minute_ago = common.get_now() - timedelta(minutes=1)
# one_hour_ago = common.get_now() - timedelta(hours=1)
# one_day_ago = common.get_now() - timedelta(days=1)
#
# if not PubSubMessage.objects.filter(
#         message_type=PubSubMessageType.EVERY_MINUTE,
#         created_at__lt=one_minute_ago
# ).exists():
#     PubSubManager.publish(PubSubMessageType.EVERY_MINUTE)
#
# if not PubSubMessage.objects.filter(
#         message_type=PubSubMessageType.EVERY_HOUR,
#         created_at__lt=one_hour_ago
# ).exists():
#     PubSubManager.publish(PubSubMessageType.EVERY_HOUR)
#
# if not PubSubMessage.objects.filter(
#         message_type=PubSubMessageType.EVERY_DAY,
#         created_at__lt=one_day_ago
# ).exists():
#     PubSubManager.publish(PubSubMessageType.EVERY_DAY)


def log(msg, prefix, suffix):
    common.log_info(f'{prefix} {msg} {suffix} ')
