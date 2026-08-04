from __future__ import annotations

import base64
import json

import httpx
import pytest

from cubebot.transmission_rpc import TransmissionError, TransmissionRPC


def _response(arguments: dict | None = None) -> httpx.Response:
    return httpx.Response(200, json={"result": "success", "arguments": arguments or {}})


@pytest.mark.asyncio
async def test_retries_request_after_session_handshake() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(409, headers={"X-Transmission-Session-Id": "new-session"})
        return _response({"version": "4.0.6", "rpc-version": 17, "rpc-version-minimum": 14})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    rpc = TransmissionRPC("http://transmission/rpc", client=client)

    info = await rpc.session_info()

    assert info.version == "4.0.6"
    assert info.rpc_version == 17
    assert len(requests) == 2
    assert requests[1].headers["X-Transmission-Session-Id"] == "new-session"
    await client.aclose()


@pytest.mark.asyncio
async def test_refreshes_session_id_when_transmission_restarts_during_retry() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(409, headers={"X-Transmission-Session-Id": "first-session"})
        if len(requests) == 2:
            return httpx.Response(409, headers={"X-Transmission-Session-Id": "second-session"})
        return _response({"version": "4.0.6"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    rpc = TransmissionRPC("http://transmission/rpc", client=client)

    info = await rpc.session_info()

    assert info.version == "4.0.6"
    assert len(requests) == 3
    assert requests[-1].headers["X-Transmission-Session-Id"] == "second-session"
    await client.aclose()


@pytest.mark.asyncio
async def test_add_metainfo_uses_base64_without_url_download() -> None:
    sent_payload: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent_payload.update(json.loads(request.content))
        return _response(
            {
                "torrent-added": {
                    "id": 7,
                    "hashString": "a" * 40,
                    "name": "example.torrent",
                }
            }
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    rpc = TransmissionRPC("http://transmission/rpc", client=client)

    added = await rpc.add_metainfo(b"d3:foo3:bare")

    assert added.name == "example.torrent"
    assert added.duplicate is False
    assert sent_payload["method"] == "torrent-add"
    assert sent_payload["arguments"] == {"metainfo": base64.b64encode(b"d3:foo3:bare").decode("ascii")}
    await client.aclose()


@pytest.mark.asyncio
async def test_duplicate_torrent_is_reported_without_error() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: _response(
                {
                    "torrent-duplicate": {
                        "id": 7,
                        "hashString": "b" * 40,
                        "name": "duplicate.torrent",
                    }
                }
            )
        )
    )
    rpc = TransmissionRPC("http://transmission/rpc", client=client)

    added = await rpc.add_magnet("magnet:?xt=urn:btih:" + "b" * 40)

    assert added.duplicate is True
    await client.aclose()


@pytest.mark.asyncio
async def test_rejects_invalid_hash_in_torrent_list() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: _response(
                {
                    "torrents": [
                        {
                            "id": 7,
                            "hashString": "not-a-hash",
                            "name": "bad",
                            "status": 4,
                        }
                    ]
                }
            )
        )
    )
    rpc = TransmissionRPC("http://transmission/rpc", client=client)

    with pytest.raises(TransmissionError):
        await rpc.list_torrents()
    await client.aclose()


@pytest.mark.asyncio
async def test_rejects_invalid_rpc_result() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"result": "failed"}))
    )
    rpc = TransmissionRPC("http://transmission/rpc", client=client)

    with pytest.raises(TransmissionError):
        await rpc.session_info()
    await client.aclose()


@pytest.mark.asyncio
async def test_remove_can_keep_or_delete_data() -> None:
    payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return _response()

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    rpc = TransmissionRPC("http://transmission/rpc", client=client)

    await rpc.remove("c" * 40)
    await rpc.remove("c" * 40, delete_data=True)

    assert payloads[0]["arguments"] == {"ids": ["c" * 40], "delete-local-data": False}
    assert payloads[1]["arguments"] == {"ids": ["c" * 40], "delete-local-data": True}
    await client.aclose()
