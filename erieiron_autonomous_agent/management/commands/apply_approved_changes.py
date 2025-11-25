import logging
from django.core.management.base import BaseCommand
from erieiron_autonomous_agent.models import ConversationChange
from erieiron_autonomous_agent.change_application_engine import ChangeApplicationEngine

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Apply all approved but not yet applied conversation changes'

    def handle(self, *args, **options):
        # Find approved changes not yet applied
        pending_changes = ConversationChange.objects.filter(
            approved=True,
            applied=False
        )

        self.stdout.write(f"Found {pending_changes.count()} pending changes to apply")

        for change in pending_changes:
            try:
                self.stdout.write(f"Applying change {change.id}...")
                ChangeApplicationEngine.apply_approved_change(change)
                self.stdout.write(self.style.SUCCESS(f"✓ Applied change {change.id}"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"✗ Failed to apply change {change.id}: {e}"))
                logger.exception(e)

        self.stdout.write(self.style.SUCCESS("Done applying changes"))
