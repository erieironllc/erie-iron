# erieiron_common/apps.py
import os
import sys
import threading
import time

from django.apps import AppConfig
from django.core.signals import request_started

import settings


class ErieironCommonConfig(AppConfig):
    name = 'erieiron_common'
    _pubsub_lock = threading.Lock()
    _pubsub_started = False

    def ready(self):
        if 'runserver' in sys.argv:
            self._ensure_pubsub_started()
            return

        request_started.connect(
            self._start_pubsub_on_first_request,
            dispatch_uid='erieiron_common.pubsub_on_first_request'
        )

    def _start_pubsub_on_first_request(self, **_):
        request_started.disconnect(dispatch_uid='erieiron_common.pubsub_on_first_request')
        self._ensure_pubsub_started()

    def _ensure_pubsub_started(self):
        with self._pubsub_lock:
            if self._pubsub_started:
                return

            thread = threading.Thread(target=self._init_pubsub, name='pubsub-init', daemon=True)
            thread.start()
            self._pubsub_started = True

    @staticmethod
    def _init_pubsub():
        time.sleep(5)  # let system warm up before handling messages
        if settings.START_MESSAGE_QUEUE_PROCESSOR:
            from erieiron_common.message_queue.pubsub_manager import init_pubsub_from_cmd_options
            pubsub_manager = init_pubsub_from_cmd_options()
            print(f"Starting webservice with a pubsub listener {pubsub_manager.get_handler()}")
        else:
            from erieiron_common.message_queue.pubsub_manager import PubSubManager
            from erieiron_common import common
            PubSubManager.get_instance().initialize(
                env=settings.MESSAGE_QUEUE_ENV or common.get_machine_name()
            )
            print(
                "Starting webservice pid={pid}.  Messages will be published to {env}".format(
                    pid=os.getpid(),
                    env=PubSubManager.get_instance().get_environment()
                )
            )
