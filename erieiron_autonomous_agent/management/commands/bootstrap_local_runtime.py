from django.core.management.base import BaseCommand, CommandError
from django.db import connection

import settings
from erieiron_autonomous_agent.models import Business
from erieiron_common.local_runtime import (
    ensure_local_auth_identity,
    get_local_auth_config,
    get_local_secrets_path,
    is_local_runtime,
)
from erieiron_common.models import Person
from erieiron_common.secret_utils import get_local_secret


class Command(BaseCommand):
    help = "Bootstrap and verify the local Erie Iron runtime."

    def add_arguments(self, parser):
        parser.add_argument(
            "--verify-only",
            action="store_true",
            help="Verify the local runtime without creating the default admin identity.",
        )

    def handle(self, *args, **options):
        if not is_local_runtime() and settings.ERIEIRON_RUNTIME_PROFILE != "local":
            raise CommandError(
                "bootstrap_local_runtime must be run with the local runtime profile "
                "(for example ERIEIRON_ENV=dev_local)."
            )

        self._verify_database_connection()
        self._verify_local_auth_config()
        self._verify_local_secrets()

        business = Business.get_erie_iron_business()
        system_person = Person.get_system_person()

        self.stdout.write(self.style.SUCCESS("Verified local runtime configuration."))
        self.stdout.write(f"Business: {business.name}")
        self.stdout.write(f"System account: {system_person.email}")

        if options["verify_only"]:
            return

        _, person = ensure_local_auth_identity()
        self.stdout.write(self.style.SUCCESS("Local admin identity is ready."))
        self.stdout.write(f"Login email: {person.email}")

    def _verify_database_connection(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()

    def _verify_local_auth_config(self):
        auth_config = get_local_auth_config()
        if not auth_config["enabled"]:
            raise CommandError("LOCAL_AUTH_ENABLED must be true for the local runtime.")
        if not auth_config["email"]:
            raise CommandError("LOCAL_AUTH_EMAIL must be configured.")
        if not auth_config["password"]:
            raise CommandError("LOCAL_AUTH_PASSWORD must be configured.")

    def _verify_local_secrets(self):
        secrets_path = get_local_secrets_path()
        if not secrets_path.exists():
            raise CommandError(f"Local secrets file not found: {secrets_path}")

        llm_api_keys = get_local_secret("LLM_API_KEYS")
        if "OPENAI" not in llm_api_keys:
            raise CommandError("LLM_API_KEYS in the local secrets file must include OPENAI.")
