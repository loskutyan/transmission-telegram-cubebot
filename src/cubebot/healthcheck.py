"""Docker healthcheck: verify that the local RPC endpoint is reachable."""

from __future__ import annotations

import asyncio

from cubebot.config import ConfigurationError, Settings
from cubebot.transmission_rpc import TransmissionError, TransmissionRPC


async def check() -> int:
    """Return a process status code for Transmission RPC availability."""
    try:
        settings = Settings.from_environment(require_telegram=False)
    except ConfigurationError:
        return 2

    rpc = TransmissionRPC(
        settings.transmission_rpc_url,
        username=settings.transmission_rpc_username,
        password=settings.transmission_rpc_password,
        timeout_seconds=settings.rpc_timeout_seconds,
    )
    try:
        await rpc.session_info()
    except TransmissionError:
        return 1
    finally:
        await rpc.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(check()))
