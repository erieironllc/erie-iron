from django.core.management.base import BaseCommand
import json

from erieiron_autonomous_agent.models import Business, InfrastructureStack
from erieiron_common.llm_apis import llm_interface


class Command(BaseCommand):
    def handle(self, env_type=None, *args, **options):
        stack = InfrastructureStack.objects.get(id="e85a1ab6-3fc1-4083-b6db-11e8f6448428")
        print(stack.stack_configuration)
        # stack.delete_resources(force=True).delete()