from __future__ import annotations

from types import SimpleNamespace

import pytest

from cubebot.bot import _PAGE_SIZE, TelegramBot, _torrent_keyboard, _torrent_page
from cubebot.config import Settings
from cubebot.transmission_rpc import AddedTorrent, Torrent


class FakeMessage:
    def __init__(self, *, filename: str = "example.torrent", size: int = 16) -> None:
        self.document = SimpleNamespace(file_name=filename, file_size=size, file_id="file-id")
        self.text: str | None = None
        self.replies: list[tuple[str, dict[str, object]]] = []

    async def reply_text(self, text: str, **kwargs: object) -> None:
        self.replies.append((text, kwargs))


class FakeTelegramFile:
    async def download_as_bytearray(self) -> bytearray:
        return bytearray(b"d4:infod4:name4:testee")


class FakeTelegramClient:
    async def get_file(self, file_id: str) -> FakeTelegramFile:
        assert file_id == "file-id"
        return FakeTelegramFile()


class FakeRPC:
    def __init__(self) -> None:
        self.metainfo: bytes | None = None

    async def add_metainfo(self, content: bytes) -> AddedTorrent:
        self.metainfo = content
        return AddedTorrent(1, "a" * 40, "example.torrent", False)


def _settings() -> Settings:
    return Settings(
        bot_token="123:token",  # noqa: S106 - deliberately fake test credential
        allowed_user_ids=frozenset({42}),
        transmission_rpc_url="http://transmission/rpc",
        transmission_rpc_username=None,
        transmission_rpc_password=None,
        max_torrent_file_bytes=1024,
        rpc_timeout_seconds=15,
        log_level="INFO",
    )


@pytest.mark.asyncio
async def test_document_is_downloaded_and_sent_as_metainfo() -> None:
    rpc = FakeRPC()
    service = TelegramBot(_settings(), rpc)  # type: ignore[arg-type]
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(type="private"),
        effective_message=message,
    )
    context = SimpleNamespace(bot=FakeTelegramClient())

    await service.add_document(update, context)  # type: ignore[arg-type]

    assert rpc.metainfo == b"d4:infod4:name4:testee"
    assert "Торрент добавлен" in message.replies[0][0]


@pytest.mark.asyncio
async def test_unauthorised_user_cannot_submit_document() -> None:
    rpc = FakeRPC()
    service = TelegramBot(_settings(), rpc)  # type: ignore[arg-type]
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=99),
        effective_chat=SimpleNamespace(type="private"),
        effective_message=message,
    )
    context = SimpleNamespace(bot=FakeTelegramClient())

    await service.add_document(update, context)  # type: ignore[arg-type]

    assert rpc.metainfo is None
    assert message.replies[0][0] == "Доступ запрещён."


def test_delete_button_uses_hash_and_requires_second_confirmation() -> None:
    torrent = Torrent(
        id=1,
        hash_string="a" * 40,
        name="example",
        status=4,
        percent_done=0.5,
        eta_seconds=60,
        rate_download=1,
        rate_upload=2,
        size_when_done=3,
        error=0,
        error_string="",
    )

    keyboard = _torrent_keyboard(torrent)
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]

    assert f"delete:{'a' * 40}" in callbacks
    assert all(callback is not None and len(callback) <= 64 for callback in callbacks)


def test_list_is_paginated() -> None:
    torrents = tuple(
        Torrent(
            id=index,
            hash_string=f"{index:040x}",
            name=f"torrent {index}",
            status=4,
            percent_done=0.5,
            eta_seconds=60,
            rate_download=1,
            rate_upload=2,
            size_when_done=3,
            error=0,
            error_string="",
        )
        for index in range(_PAGE_SIZE + 1)
    )

    first_page, keyboard = _torrent_page(torrents, requested_page=0)

    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "Торренты 1–8 из 9" in first_page
    assert "torrent 8" not in first_page
    assert "page:1" in callbacks
