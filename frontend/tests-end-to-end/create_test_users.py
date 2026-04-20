import os
from pathlib import Path

from erieiron_common.local_runtime import (
    DEFAULT_LOCAL_CONFIG_PATH,
    DEFAULT_LOCAL_SECRETS_PATH,
    load_local_runtime_json,
    resolve_local_runtime_path,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_runtime_path(env_name: str, default_path: str) -> Path:
    configured_path = os.getenv(env_name)
    resolved_path = resolve_local_runtime_path(configured_path, default_path)
    if resolved_path.is_absolute():
        return resolved_path
    return (PROJECT_ROOT / resolved_path).resolve()


def _load_runtime_json(env_name: str, default_path: str) -> dict:
    resolved_path = _resolve_runtime_path(env_name, default_path)
    if not resolved_path.exists():
        return {}
    return load_local_runtime_json(resolved_path, env_name.lower())


def _load_local_config() -> dict:
    return _load_runtime_json("ERIEIRON_LOCAL_CONFIG_FILE", DEFAULT_LOCAL_CONFIG_PATH)


def _load_local_secrets() -> dict:
    return _load_runtime_json("ERIEIRON_LOCAL_SECRETS_FILE", DEFAULT_LOCAL_SECRETS_PATH)


def resolve_local_admin_email() -> str:
    local_config = _load_local_config()
    return str(
        os.getenv("LOCAL_ADMIN_EMAIL")
        or local_config.get("LOCAL_ADMIN_EMAIL")
        or "local-admin@erieiron.local"
    ).strip()


def resolve_local_auth_password() -> str:
    local_secrets = _load_local_secrets()
    local_auth = local_secrets.get("LOCAL_AUTH") if isinstance(local_secrets, dict) else {}
    return str(
        os.getenv("LOCAL_AUTH_PASSWORD")
        or (local_auth.get("PASSWORD") if isinstance(local_auth, dict) else "")
        or ""
    )


TEST_EMAIL = resolve_local_admin_email()
TEST_PASSWORD = resolve_local_auth_password()


def generate_test_email() -> str:
    return TEST_EMAIL


def login_user_via_ui(page, base_url: str, email: str | None = None, password: str | None = None) -> str:
    resolved_email = (email or TEST_EMAIL).strip() or TEST_EMAIL
    resolved_password = password or TEST_PASSWORD
    login_url = f"{base_url.rstrip('/')}/login/"

    page.goto(login_url)

    if page.locator("#login-email").count() == 0:
        return resolved_email

    page.locator("#login-email").fill(resolved_email)
    page.locator("#login-password").fill(resolved_password)
    page.get_by_role("button", name="Sign In").click()
    page.wait_for_url(f"{base_url.rstrip('/')}/**", timeout=15_000)
    return resolved_email


def register_user_via_ui(page, base_url: str, email: str | None = None) -> str:
    return login_user_via_ui(page, base_url, email=email)
