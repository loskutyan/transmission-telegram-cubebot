"""Application entry point."""

from __future__ import annotations

import logging

from telegram import Update

from cubebot.bot import build_application
from cubebot.config import ConfigurationError, Settings
from cubebot.transmission_rpc import TransmissionRPC


class SecretRedactingFilter(logging.Filter):
    """Remove known secrets from any records emitted by application dependencies."""

    def __init__(self, *secrets: str | None) -> None:
        super().__init__()
        self._secrets = tuple(secret for secret in secrets if secret)

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact configured secrets from a log record before it is emitted."""
        record.msg = self._redact(record.msg)
        record.args = self._redact(record.args)
        return True

    def _redact(self, value: object) -> object:
        if isinstance(value, str):
            for secret in self._secrets:
                value = value.replace(secret, "<redacted>")
            return value
        if isinstance(value, tuple):
            return tuple(self._redact(item) for item in value)
        if isinstance(value, dict):
            return {key: self._redact(item) for key, item in value.items()}
        return value


def main() -> int:
    """Configure and run CubeBot until polling stops."""
    try:
        settings = Settings.from_environment()
    except ConfigurationError:
        return 2

    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    redactor = SecretRedactingFilter(
        settings.bot_token,
        settings.transmission_rpc_password,
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(redactor)
    # ``httpx`` logs full request URLs at INFO level.  Telegram puts its token in
    # that URL, so dependency request logging must not be enabled by default.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    rpc = TransmissionRPC(
        settings.transmission_rpc_url,
        username=settings.transmission_rpc_username,
        password=settings.transmission_rpc_password,
        timeout_seconds=settings.rpc_timeout_seconds,
    )
    application = build_application(settings, rpc)
    application.run_polling(allowed_updates=Update.ALL_TYPES)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
