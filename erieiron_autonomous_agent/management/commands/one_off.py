from pathlib import Path

from django.core.management.base import BaseCommand
from tree_sitter import Parser
from tree_sitter_languages import get_language

from erieiron_autonomous_agent.coding_agents.code_writer import code_validator
from erieiron_autonomous_agent.coding_agents.code_writer.code_validator import LintRules


class Command(BaseCommand):
    def handle(self, env_type=None, *args, **options):
        code_validator.validate(
            "/Users/jjschultz/src/erieiron/erieiron_ui/js/view-business-conversations.js"
        )
