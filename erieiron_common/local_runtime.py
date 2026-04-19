import json
import os
from pathlib import Path


LOCAL_RUNTIME_PROFILE = "local"
DEFAULT_LOCAL_CONFIG_PATH = "conf/config.json"
DEFAULT_LOCAL_SECRETS_PATH = "conf/secrets.json"


def is_local_runtime() -> bool:
    runtime_profile = os.getenv("ERIEIRON_RUNTIME_PROFILE")
    if runtime_profile:
        return runtime_profile.strip().lower() == LOCAL_RUNTIME_PROFILE

    try:
        config_payload = load_local_runtime_json(get_local_config_path(), "local config")
    except Exception:
        return False

    configured_profile = config_payload.get("ERIEIRON_RUNTIME_PROFILE", "")
    return str(configured_profile).strip().lower() == LOCAL_RUNTIME_PROFILE


def resolve_local_runtime_path(configured_path: str | None, default_path: str) -> Path:
    runtime_path = Path(configured_path or default_path)
    if runtime_path.is_absolute():
        return runtime_path
    return (Path.cwd() / runtime_path).resolve()


def load_local_runtime_json(path: Path, file_label: str) -> dict:
    if not path.exists():
        raise ValueError(f"{file_label} file not found: {path}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{file_label} file must be a JSON object")

    return payload


def get_local_config_path() -> Path:
    return resolve_local_runtime_path(
        os.getenv("ERIEIRON_LOCAL_CONFIG_FILE"),
        DEFAULT_LOCAL_CONFIG_PATH,
    )


def get_local_secrets_path() -> Path:
    return resolve_local_runtime_path(
        os.getenv("ERIEIRON_LOCAL_SECRETS_FILE"),
        DEFAULT_LOCAL_SECRETS_PATH,
    )


def get_local_config(config_name: str) -> dict:
    config_path = get_local_config_path()
    config_payload = load_local_runtime_json(config_path, "local config")
    if config_name not in config_payload:
        raise ValueError(
            f"config '{config_name}' not found in local config file {config_path}"
        )

    config_value = config_payload[config_name]
    if not isinstance(config_value, dict):
        raise ValueError(f"config '{config_name}' must be a JSON object")

    return config_value.copy()


def get_local_config_value(config_name: str):
    config_path = get_local_config_path()
    config_payload = load_local_runtime_json(config_path, "local config")
    if config_name not in config_payload:
        raise ValueError(
            f"config '{config_name}' not found in local config file {config_path}"
        )

    return config_payload[config_name]


def get_local_auth_config() -> dict[str, str | bool]:
    import settings

    email = settings.LOCAL_ADMIN_EMAIL.strip().lower()
    name = settings.LOCAL_AUTH_NAME.strip() or email
    return {
        "enabled": bool(settings.LOCAL_AUTH_ENABLED),
        "email": email,
        "password": settings.LOCAL_AUTH_PASSWORD,
        "name": name,
    }


def local_admin_autologin_enabled() -> bool:
    import settings

    return is_local_runtime() and not bool(settings.LOCAL_AUTH_ENABLED)


def ensure_local_admin_identity(require_password: bool = False):
    from django.contrib.auth import get_user_model
    from django.db import transaction

    from erieiron_common.enums import Role
    from erieiron_common.models import Person

    auth_config = get_local_auth_config()
    if not auth_config["email"]:
        raise ValueError("LOCAL_ADMIN_EMAIL must be configured")
    if require_password and not auth_config["password"]:
        raise ValueError("LOCAL_AUTH_PASSWORD must be configured")

    user_model = get_user_model()
    name_parts = str(auth_config["name"]).split(" ", 1)
    first_name = name_parts[0]
    last_name = name_parts[1] if len(name_parts) > 1 else ""

    with transaction.atomic():
        user, _ = user_model.objects.get_or_create(
            username=auth_config["email"],
            defaults={
                "email": auth_config["email"],
                "first_name": first_name,
                "last_name": last_name,
                "is_staff": True,
                "is_superuser": True,
            },
        )

        user_changed = False
        if user.email != auth_config["email"]:
            user.email = auth_config["email"]
            user_changed = True
        if user.first_name != first_name:
            user.first_name = first_name
            user_changed = True
        if user.last_name != last_name:
            user.last_name = last_name
            user_changed = True
        if not user.is_staff:
            user.is_staff = True
            user_changed = True
        if not user.is_superuser:
            user.is_superuser = True
            user_changed = True
        if auth_config["password"] and not user.check_password(auth_config["password"]):
            user.set_password(auth_config["password"])
            user_changed = True
        if user_changed:
            user.save()

        person = Person.objects.filter(email=auth_config["email"]).order_by("id").first()
        if person is None:
            person = Person(
                email=auth_config["email"],
                name=auth_config["name"],
                role=Role.ADMIN.value,
                django_user=user,
            )
            person.save()
        else:
            person_changed = False
            if person.name != auth_config["name"]:
                person.name = auth_config["name"]
                person_changed = True
            if person.role != Role.ADMIN.value:
                person.role = Role.ADMIN.value
                person_changed = True
            if person.django_user_id != user.id:
                person.django_user = user
                person_changed = True
            if person_changed:
                person.save()

    return user, person


def ensure_local_auth_identity():
    auth_config = get_local_auth_config()
    if not auth_config["enabled"]:
        raise ValueError("local auth is not enabled")

    return ensure_local_admin_identity(require_password=True)
