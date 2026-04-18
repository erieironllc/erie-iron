import os
from pathlib import Path


LOCAL_RUNTIME_PROFILE = "local"
LOCAL_RUNTIME_ENVS = frozenset({"dev_local", "local"})


def is_local_runtime() -> bool:
    runtime_profile = os.getenv("ERIEIRON_RUNTIME_PROFILE")
    if runtime_profile:
        return runtime_profile.strip().lower() == LOCAL_RUNTIME_PROFILE
    return os.getenv("ERIEIRON_ENV", "").strip().lower() in LOCAL_RUNTIME_ENVS


def get_local_secrets_path() -> Path:
    configured_path = os.getenv("ERIEIRON_LOCAL_SECRETS_FILE", "conf/local_secrets.json")
    secrets_path = Path(configured_path)
    if secrets_path.is_absolute():
        return secrets_path
    return (Path.cwd() / secrets_path).resolve()


def get_local_auth_config() -> dict[str, str | bool]:
    import settings

    email = settings.LOCAL_AUTH_EMAIL.strip().lower()
    name = settings.LOCAL_AUTH_NAME.strip() or email
    return {
        "enabled": bool(settings.LOCAL_AUTH_ENABLED),
        "email": email,
        "password": settings.LOCAL_AUTH_PASSWORD,
        "name": name,
    }


def ensure_local_auth_identity():
    from django.contrib.auth import get_user_model
    from django.db import transaction

    from erieiron_common.enums import Role
    from erieiron_common.models import Person

    auth_config = get_local_auth_config()
    if not auth_config["enabled"]:
        raise ValueError("local auth is not enabled")
    if not auth_config["email"]:
        raise ValueError("LOCAL_AUTH_EMAIL must be configured")
    if not auth_config["password"]:
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
        if not user.check_password(auth_config["password"]):
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
