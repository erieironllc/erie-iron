from django.core.management.base import BaseCommand
import json

from erieiron_autonomous_agent.models import Business


class Command(BaseCommand):
    def handle(self, env_type=None, *args, **options):
        for b in Business.get_portfolio_business().exclude(required_credentials__isnull=True):
            print(b.name, json.dumps(b.required_credentials, indent=4))
