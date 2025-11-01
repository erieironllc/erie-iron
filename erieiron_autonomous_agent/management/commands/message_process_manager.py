import faulthandler
import logging
import os
import signal
import subprocess
import time
import warnings

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import QuerySet

from erieiron_common import common, aws_utils, models
from erieiron_ml import gpu_utils
from erieiron_common.common import parse_bool, get_now
from erieiron_common.enums import PubSubHandlerInstanceStatus, PubSubMessagePriority, SystemCapacity, AutoScalingGroup, ScaleAction, PubSubMessageStatus
from erieiron_common.message_queue.pubsub_manager import PubSubManager, MESSAGE_HANDLER_MAX_RETRIES
from erieiron_common.message_queue.resource_manager import get_db_capacity, get_system_capacity
from erieiron_common.models import PubSubHanderInstanceProcess, PubSubHanderInstance, PubSubMessage, PubSubEnvironment
from erieiron_common.runtime_config import RuntimeConfig

warnings.filterwarnings("ignore", category=FutureWarning, module="tree_sitter")

class Command(BaseCommand):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.handler_id = None
        self.message_processor_command_name = None
        self.log_prefix = None
        self.log_suffix = None

    def add_arguments(self, parser):
        parser.add_argument(
            '--process_count',
            type=int,
            required=False,
            default=None
        )

        parser.add_argument(
            '--threads_per_process',
            type=int,
            required=False,
            default=None
        )

        parser.add_argument(
            '--job_limits_def',
            type=str,
            required=False,
            default=None
        )

        parser.add_argument(
            '--reset_host',
            help='start with a fresh instance metadata',
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
            '--env',
            help='the environment to pull messages from.  defaults to value in settings',
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

        parser.add_argument(
            '--run_isolated',
            help='run the message processor in an isolated environment - for testing purposes.  in prod this is False',
            type=parse_bool,
            required=False,
            default=False
        )

    def handle(self, *args, **options):
        rt = RuntimeConfig.instance()

        # TODO IMPLEMENT THIS here
        # process = self.get_process()
        # if process.first_heard_from:
        #     random_minutes = max(45, int(180 * random.random()))
        #     age_timedelta = timezone.now() - process.first_heard_from
        #     if age_timedelta > timedelta(minutes=random_minutes):
        #         common.log_info(f"will kill process {process}: {process.pid} after it's finished - regular process cycling: now: {timezone.now()}; first heard from: {process.first_heard_from};  {age_timedelta.seconds // 60} mins old; {random_minutes} cutoff")
        #         with transaction.atomic():
        #             PubSubHanderInstanceProcess.objects.filter(id=process.id).update(
        #                 process_status=PubSubHandlerInstanceStatus.KILL_REQUESTED
        #             )
        #         self.thread_manager.stop_all()
        #         return

        faulthandler.enable()

        signal.signal(signal.SIGTERM, self.handle_shutdown)
        signal.signal(signal.SIGINT, self.handle_shutdown)

        debug_output = options.get('debug_output')
        run_isolated = options.get('run_isolated')
        reset_host = options.get("reset_host")
        instance_id = common.default_str(options.get("instance_id"), common.get_machine_name())

        with transaction.atomic():
            instance = models.PubSubHanderInstance.objects.filter(id=instance_id).first()
            if instance:
                if (opts_process_count := options.get('process_count')) is not None:
                    instance.desired_process_count = opts_process_count

                if (job_limits_def := options.get('job_limits_def')) is not None:
                    instance.job_limits_def = job_limits_def

                if (threads_per_process := options.get('threads_per_process')) is not None:
                    instance.threads_per_process = threads_per_process

                instance.save()

        env = options.get('env')
        with transaction.atomic():
            if env:
                env = PubSubEnvironment.objects.get_or_create(id=env)[0]
            else:
                env = PubSubManager.get_env(run_isolated)

        env = PubSubEnvironment.objects.get(id=env.id)

        if PubSubHanderInstance.objects.filter(id=instance_id).exists():
            handler = PubSubHanderInstance.objects.get(id=instance_id)
            PubSubHanderInstance.objects.filter(id=handler.id).update(
                instance_status=PubSubHandlerInstanceStatus.AVAILABLE,
                compute_device=gpu_utils.get_compute_device(),
                environment=env,
                env=env.id
            )
        else:
            handler = PubSubHanderInstance.objects.create(
                instance_status=PubSubHandlerInstanceStatus.AVAILABLE,
                compute_device=gpu_utils.get_compute_device(),
                id=instance_id,
                environment=env,
                env=env.id
            )

        self.log_prefix = f"{handler}"
        self.log_suffix = ""

        common.log_info(f"""
        
        ----------------------------------------------------------------------------
        The WORLD FAMOUS Erie Iron Message Process Manager is at your service
        
        My job is as follows
        a) make sure we always have the right number of message_processor_daemon's running
        b) look for killed processes and instances.  if I find them, clean them up
        c) scale up message processor instances if I notice messages are backing up
        D) scale down message processor instances if I notice we have more than needed
        
        {self.log_suffix}

        Env                {env}
        Instance           {handler.id}
        Job Type Limits    {common.default_str(handler.job_limits_def, 'no limits')}
        Desired Proc Count {handler.desired_process_count}
        Threads per Proc   {handler.threads_per_process}
        
        GREAT... https://www.youtube.com/watch?v=dDseexwqm5U&t=110s
        ----------------------------------------------------------------------------


        """)

        self.message_processor_command_name = "message_processor_daemon"
        self.handler_id = handler.id

        try:
            idx = 0
            while True:
                try:
                    self.manage_hung_messages(env)

                    system_capacity, explanation = get_system_capacity()
                    handler.ping(system_capacity, explanation)
                    PubSubHanderInstance.cleanup_dead_instances(handler.env)

                    if env.id == "production":
                        self.manage_handler_instances(env)

                    current_processes = self.align_dbprocesses_with_running_processes()

                    count_processes_needed, count_processes_excess = self.get_needed_or_excess_count(
                        current_processes
                    )

                    if count_processes_excess > 0:
                        self.log(f'have {count_processes_excess} excess processes.  will kill the excess')
                        self.kill_excess_processes(
                            count_processes_excess
                        )
                    elif count_processes_needed > 0:
                        # only start one at a time - let the db connections catch up before starting more
                        self.start_process(debug_output)

                    current_processes = self.align_dbprocesses_with_running_processes()

                    self.kill_requested_processes(handler)

                    current_processes = self.align_dbprocesses_with_running_processes()

                    self.align_process_priorities(current_processes)

                except Exception as e:
                    logging.exception(e)
                finally:
                    idx += 1
                    time.sleep(1)

        except KeyboardInterrupt:
            self.log(f'message_queue_processor stopped for env {env}')
            exit(0)

    def manage_hung_messages(self, env):
        hung_message_ids = PubSubManager.get_hung_message_ids(env)
        timed_out_messages = []

        with transaction.atomic():
            for message in PubSubMessage.objects.select_for_update(
                    skip_locked=True
            ).filter(
                id__in=hung_message_ids,
                env=env,
                status=PubSubMessageStatus.PROCESSING
            ):
                message: PubSubMessage = message
                retry_count = common.default(message.retry_count, 0) + 1

                if retry_count > MESSAGE_HANDLER_MAX_RETRIES:
                    timed_out_messages.append(message.id)
                else:
                    # back on the queue for retry
                    PubSubMessage.objects.filter(
                        id=message.pk,
                        env=env,
                        status=PubSubMessageStatus.PROCESSING
                    ).update(
                        error_message="Message seemed hung up.  Trying again",
                        status=PubSubMessageStatus.PENDING,
                        retry_count=retry_count
                    )

        for message in models.PubSubMessage.objects.filter(
                id__in=timed_out_messages,
                env=env,
                status=PubSubMessageStatus.PROCESSING
        ):
            PubSubMessage.mark_finished(
                message.id,
                PubSubMessageStatus.FAILED,
                message.handler_instance_id,
                "Message timed out"
            )

    @transaction.atomic
    def manage_handler_instances(self, environment: PubSubEnvironment):
        q = PubSubEnvironment.objects.select_for_update(
            skip_locked=True
        ).filter(
            id=environment.id
        )
        environment: PubSubEnvironment = q.first()
        if not environment:
            return

        current_capacity, min_size, max_size = aws_utils.get_asg_size(AutoScalingGroup.MESSAGE_PROCESSOR)

        scale_action = PubSubHanderInstance.get_instance_scaling_needs(
            environment,
            current_asg_capacity=current_capacity
        )

        if ScaleAction.NO_CHANGE.eq(scale_action):
            return

        new_capacity = current_capacity
        if ScaleAction.INCREASE.eq(scale_action):
            new_capacity = min(max_size, current_capacity + 1)
        elif ScaleAction.DECREASE.eq(scale_action):
            new_capacity = max(1, current_capacity - 1)
        elif ScaleAction.GO_TO_ZERO.eq(scale_action):
            new_capacity = min_size

        if current_capacity == new_capacity:
            return

        minutes_since_last_udpate = (get_now() - common.default(environment.last_requested_increase, get_now())).total_seconds() / 60.0

        if current_capacity == 0 and new_capacity > 0 and ScaleAction.INCREASE.eq(scale_action):
            # if we are at zero, scale up immediately
            environment.set_desired_instance_count(new_capacity, f"1. current capacity is {current_capacity}, new capacity is {new_capacity}, scale action is {scale_action}")
        elif new_capacity == min_size:
            # if we're going to zero, just do it now too
            environment.set_desired_instance_count(new_capacity, f"2. current capacity is {current_capacity}, min_size is {min_size}, scale action is {scale_action}")
        elif ScaleAction.INCREASE.eq(scale_action) and current_capacity < max_size and minutes_since_last_udpate > 15:
            # wait 15 minutes since last change before scaling up
            environment.set_desired_instance_count(new_capacity, f"3. scale action is {scale_action}, minutes_since_last_udpate is {minutes_since_last_udpate}")
        elif ScaleAction.DECREASE.eq(scale_action) and current_capacity > 0 and minutes_since_last_udpate > 5:
            # wait 5 minutes since last change before scaling down.
            # not sure why I chose 5 hear and 15 for scale up.
            # if I'm being honest it's because I'm cheap and what to save money - slow to add instances, fast to subtract
            environment.set_desired_instance_count(new_capacity, f"4. scale action is {scale_action}, minutes_since_last_udpate is {minutes_since_last_udpate}")

    def align_process_priorities(self, current_processes):
        priority_handler_def = {
            PubSubMessagePriority(s.split(":")[0].strip()): int(s.split(":")[1].strip())
            for s in RuntimeConfig.instance().get_list("INGESTION_PROCESS_PRIORITY_HANDLERS")
        }

        with transaction.atomic():
            for priority, count in priority_handler_def.items():
                filtered_current_processes: QuerySet[PubSubHanderInstanceProcess] = current_processes.filter(exclusive_priority=priority)
                count_with_priority = filtered_current_processes.count()
                if count_with_priority > count:
                    proc_ids = [proc.id for proc in filtered_current_processes[0:int(count_with_priority - count)]]
                    PubSubHanderInstanceProcess.objects.filter(id__in=proc_ids).update(
                        exclusive_priority=None
                    )
                elif count_with_priority < count:
                    proc_ids = [proc.id for proc in current_processes[0:int(count - count_with_priority)]]
                    PubSubHanderInstanceProcess.objects.filter(id__in=proc_ids).update(
                        exclusive_priority=priority
                    )

    def kill_requested_processes(self, handler):
        for proc in PubSubHanderInstanceProcess.objects.filter(
                handler_instance_id=handler.id,
                cmd__contains="message_processor_daemon",
                process_status=PubSubHandlerInstanceStatus.KILL_REQUESTED
        ):
            messages = proc.get_inprogress_messages()
            if not messages.exists():
                PubSubHanderInstanceProcess.objects.filter(id=proc.id).delete()
                common.log_info(f'KILLING {proc.pid}! intentionally, so no need to be alarmed.  (unless of course you are proc {proc.pid})')
                common.kill_pid(proc.pid)
            else:
                common.log_info(f'waiting to kill {proc.pid} as it still has some messages: {", ".join([str(m) for m in messages])}')

    def get_needed_or_excess_count(self, current_processes):
        handler = self.get_handler()
        desired_process_count = handler.desired_process_count

        if PubSubHandlerInstanceStatus.NOT_AVAILABLE.eq(handler.instance_status):
            return 0, current_processes.count()

        processes_needed = max(0, desired_process_count - current_processes.count())
        processes_excess = max(0, current_processes.count() - desired_process_count)

        db_capacity, explanation = get_db_capacity()
        if SystemCapacity.CAPPED.eq(db_capacity):
            processes_needed = 0
        elif SystemCapacity.OVERLOAD.eq(db_capacity):
            processes_needed = 0
            if processes_excess == 0:
                # too many db connections used.  kill a process if not already
                processes_excess = 1

        return processes_needed, processes_excess

    def align_dbprocesses_with_running_processes(self) -> QuerySet['PubSubHanderInstanceProcess']:
        pids_running_processes = common.get_pids_by_command(self.message_processor_command_name)
        messages_ids = []
        procids_to_kill = []
        procids_to_delete = []

        handler = self.get_handler()

        if PubSubHandlerInstanceStatus.NOT_AVAILABLE.eq(handler.instance_status):
            # instance is not available. drain / kill all processes
            for proc in handler.pubsubhanderinstanceprocess_set.all():
                inprog_messages = proc.get_inprogress_messages()
                if inprog_messages.exists():
                    messages_ids += [m.id for m in inprog_messages]
                    procids_to_kill.append(proc.id)
                else:
                    procids_to_delete.append(proc.id)

        else:
            # mark as killed any processes that are no longer running
            for proc in handler.pubsubhanderinstanceprocess_set.all():
                if proc.pid not in pids_running_processes or not proc.is_alive():
                    inprog_messages = proc.get_inprogress_messages()
                    if inprog_messages.exists():
                        messages_ids += [m.id for m in inprog_messages]
                        procids_to_kill.append(proc.id)
                    else:
                        procids_to_delete.append(proc.id)

        PubSubMessage.reprocess(messages_ids, handler.env)
        PubSubHanderInstanceProcess.killkillkill(procids_to_kill)

        if procids_to_delete:
            PubSubHanderInstanceProcess.objects.filter(id__in=procids_to_delete).delete()

        for pid in pids_running_processes:
            if not PubSubHanderInstanceProcess.objects.filter(handler_instance_id=handler.id, pid=pid).exists():
                PubSubHanderInstanceProcess.get_or_create(
                    handler_instance_id=handler.id,
                    pid=pid,
                    exclusive_priority=None
                )

        return handler.pubsubhanderinstanceprocess_set.all()

    def get_handler(self):
        return PubSubHanderInstance.objects.get(id=self.handler_id)

    def start_process(self, debug_output: bool):
        handler = self.get_handler()
        if PubSubHandlerInstanceStatus.NOT_AVAILABLE.eq(handler.instance_status):
            self.log("not starting new processes as instance is unavailable")
            return

        cmd = [
            "python", "manage.py", "message_processor_daemon",
            "--instance_id", handler.id,
            "--env", handler.env
        ]

        if debug_output:
            cmd.append(f"--debug_output=True")

        process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=None,
            stderr=None,
            preexec_fn=os.setpgrp if os.name != 'nt' else None
        )

    def kill_excess_processes(self, processes_excess: int):
        if processes_excess < 1:
            return

        # too many process!  mark some for killing
        handler = self.get_handler()

        # first looking for idle processes
        killed_proc_ids = []
        for proc in handler.pubsubhanderinstanceprocess_set.filter(
                cmd__contains=self.message_processor_command_name
        ).exclude(
            exclusive_priority=PubSubMessagePriority.HIGH
        ):
            if processes_excess <= 0:
                break

            if not proc.get_inprogress_messages().exists():
                PubSubHanderInstanceProcess.killkillkill(proc.id)
                killed_proc_ids.append(proc.id)
                processes_excess -= 1

        # if we still need to kill some...
        if processes_excess > 0:
            for proc in handler.pubsubhanderinstanceprocess_set.filter(
                    cmd__contains=self.message_processor_command_name
            ).exclude(
                exclusive_priority=PubSubMessagePriority.HIGH,
                pid__in=killed_proc_ids
            ).order_by("-last_heard_from")[0:processes_excess]:
                PubSubHanderInstanceProcess.killkillkill(proc.id)
                killed_proc_ids.append(proc.id)
                processes_excess -= 1

        # ok still have more to kill, let's kill the high priorities
        if processes_excess > 0:
            for proc in handler.pubsubhanderinstanceprocess_set.filter(
                    cmd__contains=self.message_processor_command_name
            ).exclude(
                pid__in=killed_proc_ids
            ).order_by("-last_heard_from")[0:processes_excess]:
                PubSubHanderInstanceProcess.killkillkill(proc.id)

    def log(self, msg):
        common.log_info(f'{self.log_prefix} {msg} {self.log_suffix} ')

    def handle_shutdown(self, signum, frame):
        self.get_handler().shutdown(instance_is_running_on_this_machine=True)
