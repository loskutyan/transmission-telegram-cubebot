"""Small strict bencode validator used for uploaded torrent files."""

from __future__ import annotations


class BencodeError(ValueError):
    """Raised when a value is not a valid, bounded bencode document."""


type BencodeValue = bytes | int | list[BencodeValue] | dict[bytes, BencodeValue]
_MAX_DEPTH = 64


def validate_torrent(data: bytes) -> None:
    """Validate a bencoded torrent with a top-level ``info`` dictionary.

    This is deliberately a validator, not a general-purpose bencode library:
    CubeBot never needs to retain or modify the decoded payload.
    """
    value, position = _parse_value(data, 0, 0)
    if position != len(data):
        msg = "trailing data"
        raise BencodeError(msg)
    if not isinstance(value, dict) or not isinstance(value.get(b"info"), dict):
        msg = "torrent must contain a top-level info dictionary"
        raise BencodeError(msg)


def _parse_value(  # noqa: C901, PLR0912 - recursive bencode grammar is clearer as one dispatcher
    data: bytes,
    position: int,
    depth: int,
) -> tuple[BencodeValue, int]:
    if depth > _MAX_DEPTH:
        msg = "nesting is too deep"
        raise BencodeError(msg)
    if position >= len(data):
        msg = "unexpected end of data"
        raise BencodeError(msg)
    marker = data[position : position + 1]
    if marker == b"i":
        return _parse_integer(data, position)
    if marker == b"l":
        values: list[BencodeValue] = []
        position += 1
        while True:
            if position >= len(data):
                msg = "unterminated list"
                raise BencodeError(msg)
            if data[position : position + 1] == b"e":
                return values, position + 1
            value, position = _parse_value(data, position, depth + 1)
            values.append(value)
    if marker == b"d":
        values: dict[bytes, BencodeValue] = {}
        previous_key: bytes | None = None
        position += 1
        while True:
            if position >= len(data):
                msg = "unterminated dictionary"
                raise BencodeError(msg)
            if data[position : position + 1] == b"e":
                return values, position + 1
            key, position = _parse_bytes(data, position)
            if previous_key is not None and key <= previous_key:
                msg = "dictionary keys must be sorted and unique"
                raise BencodeError(msg)
            previous_key = key
            value, position = _parse_value(data, position, depth + 1)
            values[key] = value
    if marker.isdigit():
        return _parse_bytes(data, position)
    msg = "invalid bencode marker"
    raise BencodeError(msg)


def _parse_integer(data: bytes, position: int) -> tuple[int, int]:
    end = data.find(b"e", position + 1)
    if end == -1:
        msg = "unterminated integer"
        raise BencodeError(msg)
    raw = data[position + 1 : end]
    if not raw or raw == b"-0" or (raw.startswith(b"0") and len(raw) > 1):
        msg = "invalid integer"
        raise BencodeError(msg)
    digits = raw[1:] if raw.startswith(b"-") else raw
    if not digits or not digits.isdigit():
        msg = "invalid integer"
        raise BencodeError(msg)
    try:
        return int(raw), end + 1
    except ValueError as error:
        msg = "integer is too large"
        raise BencodeError(msg) from error


def _parse_bytes(data: bytes, position: int) -> tuple[bytes, int]:
    separator = data.find(b":", position)
    if separator == -1:
        msg = "missing string separator"
        raise BencodeError(msg)
    raw_length = data[position:separator]
    if not raw_length or not raw_length.isdigit() or (raw_length.startswith(b"0") and len(raw_length) > 1):
        msg = "invalid string length"
        raise BencodeError(msg)
    try:
        length = int(raw_length)
    except ValueError as error:
        msg = "string length is too large"
        raise BencodeError(msg) from error
    start = separator + 1
    end = start + length
    if end > len(data):
        msg = "string exceeds document length"
        raise BencodeError(msg)
    return data[start:end], end
