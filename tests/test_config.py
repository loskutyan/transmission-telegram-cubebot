from __future__ import annotations

import pytest

from cubebot.config import ConfigurationError, Settings


def test_settings_loads_required_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "10, 20,10")
    monkeypatch.setenv("MAX_TORRENT_FILE_BYTES", "1024")

    settings = Settings.from_environment()

    assert settings.allowed_user_ids == frozenset({10, 20})
    assert settings.max_torrent_file_bytes == 1024
    assert settings.transmission_rpc_url == "http://transmission:9091/transmission/rpc"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TELEGRAM_BOT_TOKEN", ""),
        ("TELEGRAM_ALLOWED_USER_IDS", ""),
    ],
)
def test_settings_rejects_missing_required_values(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "10")
    monkeypatch.setenv(name, value)

    with pytest.raises(ConfigurationError):
        Settings.from_environment()


def test_healthcheck_settings_do_not_require_telegram_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_IDS", raising=False)

    settings = Settings.from_environment(require_telegram=False)

    assert settings.bot_token == ""
    assert settings.allowed_user_ids == frozenset()


def test_settings_require_both_rpc_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "10")
    monkeypatch.setenv("TRANSMISSION_RPC_USERNAME", "user")
    monkeypatch.delenv("TRANSMISSION_RPC_PASSWORD", raising=False)

    with pytest.raises(ConfigurationError, match="must be set together"):
        Settings.from_environment()


def test_settings_reject_invalid_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "10")
    monkeypatch.setenv("LOG_LEVEL", "VERBOSE")

    with pytest.raises(ConfigurationError, match="standard Python log level"):
        Settings.from_environment()
