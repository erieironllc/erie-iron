import json

from erieiron_common import settings_utils


def test_get_platform_environment_overrides_merges_example_and_local_config(
    monkeypatch,
    tmp_path,
):
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()

    (conf_dir / "config.example.json").write_text(
        json.dumps(
            {
                "APPLICATION_REPO": "https://github.com/example/default",
                "DEBUG": False,
                "BASE_URL": "http://localhost:8020",
            }
        ),
        encoding="utf-8",
    )
    (conf_dir / "config.json").write_text(
        json.dumps(
            {
                "APPLICATION_REPO": "https://github.com/example/local",
                "DEBUG": True,
                "MESSAGE_QUEUE_ENV": "dev",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ERIEIRON_LOCAL_CONFIG_FILE", raising=False)

    overrides = settings_utils.get_platform_environment_overrides()

    assert overrides == {
        "DEBUG": "true",
        "BASE_URL": "http://localhost:8020",
        "MESSAGE_QUEUE_ENV": "dev",
    }


def test_get_config_uses_platform_config_values_without_env_files(
    monkeypatch,
    tmp_path,
):
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()

    (conf_dir / "config.example.json").write_text(
        json.dumps(
            {
                "BASE_URL": "http://localhost:8020",
                "DEBUG": False,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ERIEIRON_LOCAL_CONFIG_FILE", raising=False)
    monkeypatch.delenv("BASE_URL", raising=False)
    monkeypatch.delenv("DEBUG", raising=False)

    config = settings_utils.get_config()

    assert config.get("BASE_URL") == "http://localhost:8020"
    assert config.get("DEBUG", cast=bool) is False


def test_get_optional_secret_value_returns_default_when_secret_missing(monkeypatch):
    monkeypatch.setattr(settings_utils, "get_secret", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("missing")))

    assert settings_utils.get_optional_secret_value("LOCAL_AUTH", "PASSWORD", "fallback") == "fallback"
