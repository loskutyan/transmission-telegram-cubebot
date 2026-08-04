from __future__ import annotations

import pytest

from cubebot.bencode import BencodeError, validate_torrent


def test_accepts_valid_torrent_metainfo() -> None:
    validate_torrent(b"d4:infod4:name4:testee")


@pytest.mark.parametrize(
    "payload",
    [
        b"d3:foo3:bare",  # No info dictionary.
        b"d4:infod4:name4:teste",  # Missing outer terminator.
        b"d4:infoi1ee",  # info is not a dictionary.
        b"d4:zeta1:a4:infoi1ee",  # Dictionary keys are not sorted.
    ],
)
def test_rejects_invalid_torrent_metainfo(payload: bytes) -> None:
    with pytest.raises(BencodeError):
        validate_torrent(payload)
