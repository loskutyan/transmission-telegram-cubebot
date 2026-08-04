from __future__ import annotations

import logging

from cubebot.__main__ import SecretRedactingFilter


def test_log_filter_redacts_token_and_password() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="request token=%s password=%s",
        args=("token-value", "password-value"),
        exc_info=None,
    )

    SecretRedactingFilter("token-value", "password-value").filter(record)

    assert record.getMessage() == "request token=<redacted> password=<redacted>"
