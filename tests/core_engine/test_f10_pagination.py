from __future__ import annotations

import struct

import pytest

from mootdx_next.api.clients import F10_CONTENT_PAGE_SIZE
from mootdx_next.api.clients import SyncClient
from mootdx_next.errors import ProtocolDecodeError
from mootdx_next.protocol import StdQuoteProtocol
from tests.core_engine.test_sync_client_stock_apis import RecordingConnectionPool
from tests.core_engine.test_sync_client_stock_apis import RecordingScheduler
from tests.core_engine.test_sync_client_stock_apis import RecordingTransport


def _response(content: bytes) -> bytes:
    return b"\x00" * 10 + struct.pack("<H", len(content)) + content


def _client(responses: list[bytes]) -> tuple[SyncClient, RecordingTransport]:
    transport = RecordingTransport(responses=responses)
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)
    return client, transport


def test_f10_content_range_pages_and_preserves_split_gbk_character() -> None:
    raw = b"A" * (F10_CONTENT_PAGE_SIZE - 1) + "中".encode("gbk") + b"B" * 7
    client, transport = _client(
        [
            _response(raw[:F10_CONTENT_PAGE_SIZE]),
            _response(raw[F10_CONTENT_PAGE_SIZE:]),
        ]
    )

    content = client.f10_content_range("sh600036", "600036.txt", 123, len(raw))

    assert content == raw
    assert "中" in content.decode("gbk")
    first_start, first_length = struct.unpack_from("<II", transport.sent_payloads[0], 102)
    second_start, second_length = struct.unpack_from("<II", transport.sent_payloads[1], 102)
    assert (first_start, first_length) == (123, F10_CONTENT_PAGE_SIZE)
    assert (second_start, second_length) == (
        123 + F10_CONTENT_PAGE_SIZE,
        len(raw) - F10_CONTENT_PAGE_SIZE,
    )


def test_f10_content_joins_all_bytes_before_decoding() -> None:
    raw = b"A" * (F10_CONTENT_PAGE_SIZE - 1) + "中".encode("gbk") + b"B"
    client, _ = _client(
        [
            _response(raw[:F10_CONTENT_PAGE_SIZE]),
            _response(raw[F10_CONTENT_PAGE_SIZE:]),
        ]
    )
    client.f10_categories = lambda _symbol: [  # type: ignore[method-assign]
        {
            "name": "研报评级",
            "filename": "600036.txt",
            "start": 0,
            "length": len(raw),
        }
    ]

    assert client.f10_content("sh600036", "研报评级") == raw.decode("gbk")


def test_f10_content_rejects_silent_short_read() -> None:
    client, _ = _client([_response(b"short")])
    client.f10_categories = lambda _symbol: [  # type: ignore[method-assign]
        {"name": "资本运作", "filename": "600036.txt", "start": 0, "length": 100}
    ]

    with pytest.raises(ProtocolDecodeError, match="expected 100 bytes, got 5"):
        client.f10_content("sh600036", "资本运作")


@pytest.mark.parametrize(
    ("filename", "start", "length"),
    [
        ("", 0, 1),
        ("600036.txt", -1, 1),
        ("600036.txt", 0, -1),
        ("600036.txt", 0xFFFFFFFF, 2),
    ],
)
def test_f10_content_range_rejects_invalid_ranges(
    filename: str,
    start: int,
    length: int,
) -> None:
    client = SyncClient()

    with pytest.raises(ValueError):
        client.f10_content_range("sh600036", filename, start, length)
