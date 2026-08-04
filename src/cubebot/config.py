"""Runtime configuration for CubeBot."""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigurationError(ValueError):
    """Raised when required runtime configuration is missing or invalid."""


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        msg = f"{name} must be set"
        raise ConfigurationError(msg)
    return value


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as error:
        msg = f"{name} must be an integer"
        raise ConfigurationError(msg) from error
    if value <= 0:
        msg = f"{name} must be greater than zero"
        raise ConfigurationError(msg)
    return value


def _user_ids(raw: str) -> frozenset[int]:
    user_ids: set[int] = set()
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        try:
            user_id = int(value)
        except ValueError as error:
            msg = "TELEGRAM_ALLOWED_USER_IDS must contain integers"
            raise ConfigurationError(msg) from error
        if user_id <= 0:
            msg = "TELEGRAM_ALLOWED_USER_IDS must contain positive integers"
            raise ConfigurationError(msg)
        user_ids.add(user_id)
    if not user_ids:
        msg = "TELEGRAM_ALLOWED_USER_IDS must contain at least one user ID"
        raise ConfigurationError(msg)
    return frozenset(user_ids)


def _group_ids(raw: str) -> frozenset[int]:
    group_ids: set[int] = set()
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        try:
            group_id = int(value)
        except ValueError as error:
            msg = "TELEGRAM_ALLOWED_GROUP_IDS must contain integers"
            raise ConfigurationError(msg) from error
        if group_id >= 0:
            msg = "TELEGRAM_ALLOWED_GROUP_IDS must contain negative Telegram chat IDs"
            raise ConfigurationError(msg)
        group_ids.add(group_id)
    return frozenset(group_ids)


@dataclass(frozen=True, slots=True)
class Settings:
    """All configuration supplied through environment variables."""

    bot_token: str
    allowed_user_ids: frozenset[int]
    allowed_group_ids: frozenset[int]
    transmission_rpc_url: str
    transmission_rpc_username: str | None
    transmission_rpc_password: str | None
    max_torrent_file_bytes: int
    rpc_timeout_seconds: float
    log_level: str

    @classmethod
    def from_environment(cls, *, require_telegram: bool = True) -> Settings:
        """Load and validate runtime settings from environment variables."""
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        user_ids = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").strip()
        group_ids = os.getenv("TELEGRAM_ALLOWED_GROUP_IDS", "").strip()

        if require_telegram:
            token = _required("TELEGRAM_BOT_TOKEN")
            user_ids = _required("TELEGRAM_ALLOWED_USER_IDS")

        try:
            timeout = float(os.getenv("RPC_TIMEOUT_SECONDS", "15"))
        except ValueError as error:
            msg = "RPC_TIMEOUT_SECONDS must be a number"
            raise ConfigurationError(msg) from error
        if timeout <= 0:
            msg = "RPC_TIMEOUT_SECONDS must be greater than zero"
            raise ConfigurationError(msg)

        log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            msg = "LOG_LEVEL must be a standard Python log level"
            raise ConfigurationError(msg)

        rpc_url = os.getenv("TRANSMISSION_RPC_URL", "http://transmission:9091/transmission/rpc").strip()
        if not rpc_url:
            msg = "TRANSMISSION_RPC_URL must not be empty"
            raise ConfigurationError(msg)
        rpc_username = os.getenv("TRANSMISSION_RPC_USERNAME") or None
        rpc_password = os.getenv("TRANSMISSION_RPC_PASSWORD") or None
        if bool(rpc_username) != bool(rpc_password):
            msg = "TRANSMISSION_RPC_USERNAME and TRANSMISSION_RPC_PASSWORD must be set together"
            raise ConfigurationError(msg)

        return cls(
            bot_token=token,
            allowed_user_ids=_user_ids(user_ids) if user_ids else frozenset(),
            allowed_group_ids=_group_ids(group_ids),
            transmission_rpc_url=rpc_url,
            transmission_rpc_username=rpc_username,
            transmission_rpc_password=rpc_password,
            max_torrent_file_bytes=_positive_int("MAX_TORRENT_FILE_BYTES", 5 * 1024 * 1024),
            rpc_timeout_seconds=timeout,
            log_level=log_level,
        )
