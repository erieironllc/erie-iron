# erieiron_common/apps.py
import os
import sys
import time

from django.apps import AppConfig

import settings


class ErieironCommonConfig(AppConfig):
    name = 'erieiron_common'

    def ready(self):
        if 'runserver' in sys.argv:
            import threading

            def init_pubsub():
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
                    print(f"Starting webservice pid={os.getpid()}.  Messages will be published to {PubSubManager.get_instance().get_environment()}")

            threading.Thread(target=init_pubsub).start()
