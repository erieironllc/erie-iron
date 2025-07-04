# erieiron_common/apps.py
from django.apps import AppConfig


class ErieironCommonConfig(AppConfig):
    name = 'erieiron_common'

    def ready(self):
        import erieiron_common.models

