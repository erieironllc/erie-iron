from django.core.management.base import BaseCommand, CommandError
from django.db import connection

import settings
from erieiron_autonomous_agent.models import Business
from erieiron_common.local_runtime import (
    ensure_local_admin_identity,
    ensure_local_auth_identity,
    get_local_auth_config,
    get_local_config_value,
    get_local_secrets_path,
    is_local_runtime,
    local_admin_autologin_enabled,
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
        parser.add_argument(
            "--application-repo-url",
            dest="application_repo_url",
            help="Set the Erie Iron application repo URL for this local instance.",
        )

    def handle(self, *args, **options):
        if not is_local_runtime() and settings.ERIEIRON_RUNTIME_PROFILE != "local":
            raise CommandError(
                "bootstrap_local_runtime must be run with the local runtime profile "
                "(for example ERIEIRON_RUNTIME_PROFILE=local)."
            )

        self._verify_database_connection()
        self._verify_local_auth_config()
        self._verify_local_secrets()

        business = Business.get_erie_iron_business()
        application_repo_url = self._resolve_application_repo_url(
            options["application_repo_url"]
        )
        self._sync_application_repo_url(business, application_repo_url)
        system_person = Person.get_system_person()

        self.stdout.write(self.style.SUCCESS("Verified local runtime configuration."))
        self.stdout.write(f"Business: {business.name}")
        self.stdout.write(f"Application repo: {business.get_application_repo_url()}")
        self.stdout.write(f"System account: {system_person.email}")

        if options["verify_only"]:
            return

        if local_admin_autologin_enabled():
            _, person = ensure_local_admin_identity()
            self.stdout.write(self.style.SUCCESS("Local admin identity is ready."))
            self.stdout.write(f"Auto-login email: {person.email}")
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
        if not auth_config["email"]:
            raise CommandError("LOCAL_ADMIN_EMAIL must be configured.")

    def _verify_local_secrets(self):
        secrets_path = get_local_secrets_path()
        if not secrets_path.exists():
            raise CommandError(f"Local secrets file not found: {secrets_path}")

        llm_api_keys = get_local_secret("LLM_API_KEYS")
        if "OPENAI" not in llm_api_keys:
            raise CommandError("LLM_API_KEYS in the local secrets file must include OPENAI.")

    def _resolve_application_repo_url(self, explicit_repo_url):
        if explicit_repo_url is not None:
            return self._normalize_application_repo_url(explicit_repo_url)

        try:
            application_repo_url = get_local_config_value("APPLICATION_REPO")
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        return self._normalize_application_repo_url(application_repo_url)

    def _normalize_application_repo_url(self, repo_url):
        normalized_repo_url = str(repo_url).strip()
        if not normalized_repo_url:
            raise CommandError("The application repo URL must not be blank.")
        return normalized_repo_url

    def _sync_application_repo_url(self, business, application_repo_url):
        if business.github_repo_url == application_repo_url:
            return

        Business.objects.filter(id=business.id).update(github_repo_url=application_repo_url)
        business.refresh_from_db(fields=["github_repo_url"])
