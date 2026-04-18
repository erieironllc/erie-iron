import json

import pytest

from erieiron_common import secret_utils


def test_get_secret_reads_local_secret_file(monkeypatch, tmp_path):
    secrets_path = tmp_path / "local_secrets.json"
    secrets_path.write_text(
        json.dumps({"LLM_API_KEYS": {"OPENAI": "test-openai-key"}}),
        encoding="utf-8",
    )

    monkeypatch.setenv("ERIEIRON_RUNTIME_PROFILE", "local")
    monkeypatch.setenv("ERIEIRON_LOCAL_SECRETS_FILE", str(secrets_path))

    secret_value = secret_utils.get_secret("LLM_API_KEYS")

    assert secret_value == {"OPENAI": "test-openai-key"}


def test_get_secret_requires_local_secret_to_be_json_object(monkeypatch, tmp_path):
    secrets_path = tmp_path / "local_secrets.json"
    secrets_path.write_text(json.dumps({"LLM_API_KEYS": "bad-shape"}), encoding="utf-8")

    monkeypatch.setenv("ERIEIRON_RUNTIME_PROFILE", "local")
    monkeypatch.setenv("ERIEIRON_LOCAL_SECRETS_FILE", str(secrets_path))

    with pytest.raises(ValueError, match="must be a JSON object"):
        secret_utils.get_secret("LLM_API_KEYS")
