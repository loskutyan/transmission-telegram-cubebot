"""Small async client for the Transmission 4.0.x RPC API.

The project intentionally owns this small protocol wrapper instead of depending on
an unmaintained third-party Transmission client.  Transmission 4.0.x returns a
409 response containing ``X-Transmission-Session-Id`` on the first request; this
module handles that handshake transparently.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any, Final

import httpx

logger = logging.getLogger(__name__)

_SESSION_HEADER: Final = "X-Transmission-Session-Id"
_TORRENT_HASH_LENGTH: Final = 40


class TransmissionError(RuntimeError):
    """A safe-to-display summary of a Transmission RPC failure."""


@dataclass(frozen=True, slots=True)
class SessionInfo:
    """Transmission daemon and RPC protocol version information."""

    version: str
    rpc_version: int | None
    rpc_minimum_version: int | None


@dataclass(frozen=True, slots=True)
class Torrent:
    """Torrent fields required by the Telegram management interface."""

    id: int
    hash_string: str
    name: str
    status: int
    percent_done: float
    eta_seconds: int
    rate_download: int
    rate_upload: int
    size_when_done: int
    error: int
    error_string: str

    @property
    def status_label(self) -> str:
        """Return a human-readable Russian label for the RPC status code."""
        return {
            0: "приостановлен",
            1: "ожидает проверки",
            2: "проверяется",
            3: "ожидает загрузки",
            4: "загружается",
            5: "ожидает раздачи",
            6: "раздаётся",
        }.get(self.status, "неизвестно")

    @property
    def is_stopped(self) -> bool:
        """Return whether Transmission reports the torrent as stopped."""
        return self.status == 0


@dataclass(frozen=True, slots=True)
class AddedTorrent:
    """Result of adding new or duplicate torrent metainfo."""

    id: int
    hash_string: str
    name: str
    duplicate: bool


class TransmissionRPC:
    """Asynchronous client for the legacy-but-supported Transmission RPC format."""

    def __init__(
        self,
        url: str,
        *,
        username: str | None = None,
        password: str | None = None,
        timeout_seconds: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not url:
            msg = "Transmission RPC URL must not be empty"
            raise ValueError(msg)
        if bool(username) != bool(password):
            msg = "Transmission RPC username and password must be set together"
            raise ValueError(msg)

        self._url = url
        self._session_id: str | None = None
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            auth=httpx.BasicAuth(username, password) if username and password else None,
            # The local Docker DNS endpoint must never be sent to a proxy inherited
            # from the host environment.  In particular, proxies can see RPC Basic
            # Auth credentials and Base64 torrent metainfo.
            trust_env=False,
        )

    async def aclose(self) -> None:
        """Close the HTTP client owned by this RPC instance."""
        if self._owns_client:
            await self._client.aclose()

    async def session_info(self) -> SessionInfo:
        """Fetch daemon and RPC protocol version information."""
        arguments = await self._request("session-get", {"fields": ["version", "rpc-version", "rpc-version-minimum"]})
        return SessionInfo(
            version=str(arguments.get("version", "unknown")),
            rpc_version=_optional_int(arguments.get("rpc-version")),
            rpc_minimum_version=_optional_int(arguments.get("rpc-version-minimum")),
        )

    async def list_torrents(self) -> tuple[Torrent, ...]:
        """Fetch torrents and the fields needed by the bot interface."""
        arguments = await self._request(
            "torrent-get",
            {
                "fields": [
                    "id",
                    "hashString",
                    "name",
                    "status",
                    "percentDone",
                    "eta",
                    "rateDownload",
                    "rateUpload",
                    "sizeWhenDone",
                    "error",
                    "errorString",
                ]
            },
        )
        torrents = arguments.get("torrents")
        if not isinstance(torrents, list) or not all(isinstance(item, dict) for item in torrents):
            msg = "Transmission returned an invalid torrent list"
            raise TransmissionError(msg)
        return tuple(_torrent_from_response(item) for item in torrents)

    async def add_magnet(self, magnet: str) -> AddedTorrent:
        """Add a torrent from a magnet link."""
        if not magnet.lower().startswith("magnet:?"):
            msg = "Only magnet links are accepted"
            raise ValueError(msg)
        return await self._add({"filename": magnet})

    async def add_metainfo(self, torrent_file: bytes) -> AddedTorrent:
        """Add a torrent from raw bencoded metainfo."""
        if not torrent_file:
            msg = "Torrent file must not be empty"
            raise ValueError(msg)
        metainfo = base64.b64encode(torrent_file).decode("ascii")
        return await self._add({"metainfo": metainfo})

    async def start(self, torrent_hash: str) -> None:
        """Start the torrent identified by its full hexadecimal hash."""
        await self._request("torrent-start", {"ids": [_torrent_identifier(torrent_hash)]})

    async def stop(self, torrent_hash: str) -> None:
        """Stop the torrent identified by its full hexadecimal hash."""
        await self._request("torrent-stop", {"ids": [_torrent_identifier(torrent_hash)]})

    async def remove(self, torrent_hash: str, *, delete_data: bool = False) -> None:
        """Remove a torrent, optionally deleting its downloaded data."""
        await self._request(
            "torrent-remove",
            {
                "ids": [_torrent_identifier(torrent_hash)],
                "delete-local-data": delete_data,
            },
        )

    async def _add(self, arguments: dict[str, str]) -> AddedTorrent:
        response_arguments = await self._request("torrent-add", arguments)
        added = response_arguments.get("torrent-added")
        duplicate = False
        if not isinstance(added, dict):
            added = response_arguments.get("torrent-duplicate")
            duplicate = True
        if not isinstance(added, dict):
            msg = "Transmission did not return an added torrent"
            raise TransmissionError(msg)

        try:
            return AddedTorrent(
                id=int(added["id"]),
                hash_string=str(added["hashString"]),
                name=str(added["name"]),
                duplicate=duplicate,
            )
        except (KeyError, TypeError, ValueError) as error:
            msg = "Transmission returned an invalid added torrent"
            raise TransmissionError(msg) from error

    async def _request(self, method: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a method and retry once when Transmission issues a new session ID."""
        # Transmission can restart between the handshake and retry.  Refreshing a
        # session ID twice keeps the request bounded while covering that race.
        for _ in range(3):
            response = await self._send(method, arguments)
            if response.status_code != httpx.codes.CONFLICT:
                break
            session_id = response.headers.get(_SESSION_HEADER)
            if not session_id:
                msg = "Transmission rejected the RPC session"
                raise TransmissionError(msg)
            self._session_id = session_id
        else:
            msg = "Transmission repeatedly rejected the RPC session"
            raise TransmissionError(msg)

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            logger.warning("Transmission RPC request failed: method=%s status=%s", method, response.status_code)
            msg = "Transmission RPC request failed"
            raise TransmissionError(msg) from error

        try:
            payload = response.json()
        except ValueError as error:
            msg = "Transmission returned an invalid JSON response"
            raise TransmissionError(msg) from error
        if not isinstance(payload, dict):
            msg = "Transmission returned an invalid RPC response"
            raise TransmissionError(msg)
        if payload.get("result") != "success":
            msg = "Transmission reported an RPC error"
            raise TransmissionError(msg)

        result = payload.get("arguments", {})
        if not isinstance(result, dict):
            msg = "Transmission returned invalid RPC arguments"
            raise TransmissionError(msg)
        return result

    async def _send(self, method: str, arguments: dict[str, Any]) -> httpx.Response:
        headers = {_SESSION_HEADER: self._session_id} if self._session_id else {}
        try:
            return await self._client.post(
                self._url,
                headers=headers,
                json={"method": method, "arguments": arguments},
            )
        except httpx.HTTPError as error:
            logger.warning("Transmission RPC network error: method=%s type=%s", method, type(error).__name__)
            msg = "Cannot reach Transmission RPC"
            raise TransmissionError(msg) from error


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _torrent_identifier(torrent_hash: str) -> str:
    normalized = torrent_hash.strip().lower()
    if len(normalized) != _TORRENT_HASH_LENGTH or any(char not in "0123456789abcdef" for char in normalized):
        msg = "Torrent hash must be a 40-character hexadecimal hash"
        raise ValueError(msg)
    return normalized


def _torrent_from_response(value: dict[str, Any]) -> Torrent:
    try:
        return Torrent(
            id=int(value["id"]),
            hash_string=_torrent_identifier(str(value["hashString"])),
            name=str(value["name"]),
            status=int(value["status"]),
            percent_done=float(value.get("percentDone", 0)),
            eta_seconds=int(value.get("eta", -1)),
            rate_download=int(value.get("rateDownload", 0)),
            rate_upload=int(value.get("rateUpload", 0)),
            size_when_done=int(value.get("sizeWhenDone", 0)),
            error=int(value.get("error", 0)),
            error_string=str(value.get("errorString", "")),
        )
    except (KeyError, TypeError, ValueError) as error:
        msg = "Transmission returned an invalid torrent"
        raise TransmissionError(msg) from error
