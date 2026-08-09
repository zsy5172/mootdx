from __future__ import annotations

import json
import struct
import zlib
from decimal import Decimal
from pathlib import Path

import pytest

from compat.tdx_capture import CaptureDecodeError
from compat.tdx_capture import analyze_session
from compat.tdx_capture import encode_price
from compat.tdx_capture import find_price_encodings
from compat.tdx_capture import parse_encrypted_quotes
from compat.tdx_capture import parse_request_stream
from compat.tdx_capture import parse_response_stream


def _request(command: int, payload: bytes, *, sequence: int = 0x010C) -> bytes:
    body = struct.pack("<H", command) + payload
    return struct.pack("<HIHH", sequence, 0x01006A18, len(body), len(body)) + body


def _response(payload: bytes, *, compress: bool = False) -> bytes:
    wire_payload = zlib.compress(payload) if compress else payload
    return (
        struct.pack(
            "<IIIHH",
            0x0074CBB1,
            0x6A18010C,
            0,
            len(wire_payload),
            len(payload),
        )
        + wire_payload
    )


def _encrypted_quote_body() -> bytes:
    body = bytearray(struct.pack("<H", 1))
    body.extend(struct.pack("<B6sH", 1, b"600036", 4767))
    for value in (3959, -59, -42, 0, -64):
        body.extend(encode_price(value))
    body.extend(struct.pack("<I", 153050))
    body.extend(encode_price(59600))
    body.extend(encode_price(1050186))
    body.extend(encode_price(44239))
    body.extend(struct.pack("<f", 4_136_021_760.0))
    for value in (478608, 571579, 0, 2430498):
        body.extend(encode_price(value))
    for values in (
        (-1, 0, 35, 1589),
        (-2, 1, 27, 11679),
        (-3, 2, 167, 638),
        (-4, 3, 46, 752),
        (-5, 4, 12, 852),
    ):
        for value in values:
            body.extend(encode_price(value))
    body.extend(bytes.fromhex("561453a5020074d1fb49"))
    for _ in range(6):
        for value in (-3959, -3959, 0, 0):
            body.extend(encode_price(value))
    return bytes(value ^ 0x93 for value in body)


def test_stream_parsers_split_and_decompress_frames() -> None:
    request_data = _request(0x0547, b"one") + _request(0x0537, b"two")
    compressed_payload = b"second" * 100
    response_data = _response(b"first") + _response(compressed_payload, compress=True)

    requests = parse_request_stream(request_data)
    responses = parse_response_stream(response_data)

    assert [frame.command for frame in requests] == [0x0547, 0x0537]
    assert [frame.offset for frame in requests] == [0, len(_request(0x0547, b"one"))]
    assert [frame.payload for frame in responses] == [b"first", compressed_payload]
    assert responses[1].compressed_size < responses[1].uncompressed_size


def test_stream_parsers_reject_truncated_or_inconsistent_frames() -> None:
    with pytest.raises(CaptureDecodeError, match="trailing bytes"):
        parse_request_stream(b"short")

    inconsistent = struct.pack("<HIHH", 0x010C, 0, 1, 2) + b"x"
    with pytest.raises(CaptureDecodeError, match="lengths differ"):
        parse_request_stream(inconsistent)

    with pytest.raises(CaptureDecodeError, match="only 17 remain"):
        parse_response_stream(struct.pack("<IIIHH", 0, 0, 0, 10, 10) + b"x")


def test_parse_encrypted_quotes_decodes_real_capture_shape() -> None:
    decoded = bytes(value ^ 0x93 for value in _encrypted_quote_body())
    rows = parse_encrypted_quotes(decoded)

    assert len(decoded) == 120
    assert rows == [
        {
            "market": 1,
            "code": "600036",
            "active": 4767,
            "close": 39.59,
            "pre_close": 39.0,
            "open": 39.17,
            "high": 39.59,
            "low": 38.95,
            "server_time": "15:30:50",
            "unknown_price": 596.0,
            "volume": 1050186,
            "current_volume": 44239,
            "amount": 4136021760.0,
            "in_volume": 478608,
            "out_volume": 571579,
            "sell_amount": 0,
            "open_amount": 2430498,
            "levels": [
                {
                    "level": 1,
                    "bid": 39.58,
                    "ask": 39.59,
                    "bid_volume": 35,
                    "ask_volume": 1589,
                },
                {
                    "level": 2,
                    "bid": 39.57,
                    "ask": 39.6,
                    "bid_volume": 27,
                    "ask_volume": 11679,
                },
                {
                    "level": 3,
                    "bid": 39.56,
                    "ask": 39.61,
                    "bid_volume": 167,
                    "ask_volume": 638,
                },
                {
                    "level": 4,
                    "bid": 39.55,
                    "ask": 39.62,
                    "bid_volume": 46,
                    "ask_volume": 752,
                },
                {
                    "level": 5,
                    "bid": 39.54,
                    "ask": 39.63,
                    "bid_volume": 12,
                    "ask_volume": 852,
                },
            ],
            "tail_hex": "561453a5020074d1fb49",
            "extra_values": [-3959, -3959, 0, 0] * 6,
        }
    ]


def test_price_search_checks_absolute_and_relative_tdx_encodings() -> None:
    payload = encode_price(4290) + encode_price(4290 - 3959) + struct.pack("<f", 42.9)
    matches = find_price_encodings(
        payload,
        Decimal("42.90"),
        bases={"close": Decimal("39.59")},
    )

    assert {match["encoding"] for match in matches} == {
        "float32-le",
        "tdx-price-absolute",
        "tdx-price-delta-close",
    }


def test_analyze_session_aligns_frames_timestamps_and_filters(
    tmp_path: Path,
) -> None:
    request_payload = (
        struct.pack("<H", 1) + struct.pack("<B6s", 1, b"600036") + bytes(4)
    )
    request_data = _request(0x0547, request_payload)
    response_data = _response(_encrypted_quote_body())
    (tmp_path / "client-to-server.bin").write_bytes(request_data)
    (tmp_path / "server-to-client.bin").write_bytes(response_data)
    events = [
        {
            "kind": "data",
            "direction": "client_to_server",
            "offset": 0,
            "length": len(request_data),
            "timestamp": "2026-07-28T13:27:49+00:00",
        },
        {
            "kind": "data",
            "direction": "server_to_client",
            "offset": 0,
            "length": len(response_data),
            "timestamp": "2026-07-28T13:27:49.010000+00:00",
        },
    ]
    (tmp_path / "events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )

    artifact = analyze_session(
        tmp_path,
        commands={0x0547},
        codes={"600036"},
        search_prices=[Decimal("42.90"), Decimal("35.10")],
    )

    assert artifact["request_frame_count"] == 1
    assert artifact["matched_frame_count"] == 1
    frame = artifact["frames"][0]
    assert frame["request"]["timestamp"] == "2026-07-28T13:27:49+00:00"
    assert frame["response"]["xor_key"] == "0x93"
    assert frame["quotes"][0]["pre_close"] == 39.0
    assert frame["price_search"] == [
        {"price": "42.90", "matches": []},
        {"price": "35.10", "matches": []},
    ]
