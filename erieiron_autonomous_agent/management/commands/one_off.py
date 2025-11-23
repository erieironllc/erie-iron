from django.core.management.base import BaseCommand
import json

from erieiron_autonomous_agent.models import Business
from erieiron_common.llm_apis import llm_interface


class Command(BaseCommand):
    def handle(self, env_type=None, *args, **options):
        ...