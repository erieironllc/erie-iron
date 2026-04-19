import json
from unittest.mock import Mock, patch

import pytest
from django.core.management.base import CommandError

from erieiron_autonomous_agent.management.commands.bootstrap_local_runtime import Command
from erieiron_autonomous_agent.models import Business
from erieiron_common.enums import BusinessIdeaSource


def write_local_secrets(tmp_path, payload):
    secrets_path = tmp_path / "local_secrets.json"
    secrets_path.write_text(json.dumps(payload), encoding="utf-8")
    return secrets_path


def configure_local_runtime(monkeypatch, secrets_path):
    monkeypatch.setenv("ERIEIRON_RUNTIME_PROFILE", "local")
    monkeypatch.setenv("ERIEIRON_LOCAL_SECRETS_FILE", str(secrets_path))


def test_business_get_application_repo_url_returns_configured_value():
    business = Business(
        name="Erie Iron, LLC",
        source=BusinessIdeaSource.HUMAN,
        github_repo_url="https://github.com/example/local-app",
    )

    assert business.get_application_repo_url() == "https://github.com/example/local-app"


def test_business_get_application_repo_url_requires_value():
    business = Business(
        name="Erie Iron, LLC",
        source=BusinessIdeaSource.HUMAN,
        github_repo_url="",
    )

    with pytest.raises(ValueError, match="application repo url is not configured"):
        business.get_application_repo_url()


def test_bootstrap_local_runtime_resolves_application_repo_url_from_local_secret(
    monkeypatch,
    tmp_path,
):
    secrets_path = write_local_secrets(
        tmp_path,
        {"APPLICATION_REPO": {"url": "https://github.com/example/local-app"}},
    )
    configure_local_runtime(monkeypatch, secrets_path)

    command = Command()

    assert (
        command._resolve_application_repo_url(None)
        == "https://github.com/example/local-app"
    )


def test_bootstrap_local_runtime_prefers_explicit_application_repo_url(
    monkeypatch,
    tmp_path,
):
    secrets_path = write_local_secrets(tmp_path, {})
    configure_local_runtime(monkeypatch, secrets_path)

    command = Command()

    assert (
        command._resolve_application_repo_url("https://github.com/example/explicit-app")
        == "https://github.com/example/explicit-app"
    )


def test_bootstrap_local_runtime_requires_application_repo_url_secret_key(
    monkeypatch,
    tmp_path,
):
    secrets_path = write_local_secrets(
        tmp_path,
        {"APPLICATION_REPO": {}},
    )
    configure_local_runtime(monkeypatch, secrets_path)

    command = Command()

    with pytest.raises(CommandError, match="APPLICATION_REPO .* include url"):
        command._resolve_application_repo_url(None)


def test_bootstrap_local_runtime_syncs_application_repo_url():
    business = Business(
        id="erie-iron",
        name="Erie Iron, LLC",
        source=BusinessIdeaSource.HUMAN,
        github_repo_url="https://github.com/example/old-app",
    )
    refreshed_fields = []
    business.refresh_from_db = lambda fields=None: refreshed_fields.append(fields)

    command = Command()
    fake_queryset = Mock()

    with patch.object(Business.objects, "filter", return_value=fake_queryset) as filter_mock:
        command._sync_application_repo_url(
            business,
            "https://github.com/example/new-app",
        )

    filter_mock.assert_called_once_with(id="erie-iron")
    fake_queryset.update.assert_called_once_with(
        github_repo_url="https://github.com/example/new-app"
    )
    assert refreshed_fields == [["github_repo_url"]]
