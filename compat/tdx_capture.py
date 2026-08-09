from __future__ import annotations

import json
import re
import struct
import zlib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

REQUEST_HEADER = struct.Struct("<HIHH")
RESPONSE_HEADER = struct.Struct("<IIIHH")
COMMAND_XOR_KEYS = {
    0x0547: 0x93,
    0x054E: 0x77,
}
SIX_DIGIT_CODE = re.compile(rb"(?<!\d)(\d{6})(?!\d)")


class CaptureDecodeError(ValueError):
    """Raised when a captured TDX application stream is incomplete or malformed."""


@dataclass(frozen=True)
class RequestFrame:
    index: int
    offset: int
    raw_length: int
    sequence: int
    packet_type: int
    payload: bytes

    @property
    def command(self) -> int | None:
        if len(self.payload) < 2:
            return None
        return int.from_bytes(self.payload[:2], "little")


@dataclass(frozen=True)
class ResponseFrame:
    index: int
    offset: int
    raw_length: int
    reserved_1: int
    reserved_2: int
    reserved_3: int
    compressed_size: int
    uncompressed_size: int
    payload: bytes


@dataclass(frozen=True)
class EventSpan:
    direction: str
    offset: int
    length: int
    timestamp: str


def parse_request_stream(data: bytes) -> list[RequestFrame]:
    frames: list[RequestFrame] = []
    offset = 0
    while offset < len(data):
        remaining = len(data) - offset
        if remaining < REQUEST_HEADER.size:
            raise CaptureDecodeError(
                f"request stream has {remaining} trailing bytes at offset {offset}"
            )

        sequence, packet_type, payload_length_1, payload_length_2 = (
            REQUEST_HEADER.unpack_from(data, offset)
        )
        if payload_length_1 != payload_length_2:
            raise CaptureDecodeError(
                "request payload lengths differ at offset "
                f"{offset}: {payload_length_1} != {payload_length_2}"
            )

        raw_length = REQUEST_HEADER.size + payload_length_1
        end = offset + raw_length
        if end > len(data):
            raise CaptureDecodeError(
                f"request frame at offset {offset} needs {raw_length} bytes, "
                f"only {remaining} remain"
            )

        frames.append(
            RequestFrame(
                index=len(frames),
                offset=offset,
                raw_length=raw_length,
                sequence=sequence,
                packet_type=packet_type,
                payload=data[offset + REQUEST_HEADER.size : end],
            )
        )
        offset = end

    return frames


def parse_response_stream(data: bytes) -> list[ResponseFrame]:
    frames: list[ResponseFrame] = []
    offset = 0
    while offset < len(data):
        remaining = len(data) - offset
        if remaining < RESPONSE_HEADER.size:
            raise CaptureDecodeError(
                f"response stream has {remaining} trailing bytes at offset {offset}"
            )

        reserved_1, reserved_2, reserved_3, compressed_size, uncompressed_size = (
            RESPONSE_HEADER.unpack_from(data, offset)
        )
        raw_length = RESPONSE_HEADER.size + compressed_size
        end = offset + raw_length
        if end > len(data):
            raise CaptureDecodeError(
                f"response frame at offset {offset} needs {raw_length} bytes, "
                f"only {remaining} remain"
            )

        wire_payload = data[offset + RESPONSE_HEADER.size : end]
        if compressed_size == uncompressed_size:
            payload = wire_payload
        else:
            try:
                payload = zlib.decompress(wire_payload)
            except zlib.error as exc:
                raise CaptureDecodeError(
                    f"response frame at offset {offset} has invalid zlib data"
                ) from exc
            if len(payload) != uncompressed_size:
                raise CaptureDecodeError(
                    f"response frame at offset {offset} decoded to {len(payload)} bytes, "
                    f"expected {uncompressed_size}"
                )

        frames.append(
            ResponseFrame(
                index=len(frames),
                offset=offset,
                raw_length=raw_length,
                reserved_1=reserved_1,
                reserved_2=reserved_2,
                reserved_3=reserved_3,
                compressed_size=compressed_size,
                uncompressed_size=uncompressed_size,
                payload=payload,
            )
        )
        offset = end

    return frames


def decode_command_payload(command: int | None, payload: bytes) -> bytes:
    key = COMMAND_XOR_KEYS.get(command)
    if key is None:
        return payload
    return bytes(value ^ key for value in payload)


def extract_request_symbols(frame: RequestFrame) -> list[dict[str, object]]:
    command = frame.command
    payload = frame.payload
    if command == 0x0547 and len(payload) >= 4:
        count = int.from_bytes(payload[2:4], "little")
        expected = 4 + count * 11
        if expected == len(payload):
            symbols = []
            position = 4
            for _ in range(count):
                symbols.append(
                    {
                        "market": payload[position],
                        "code": payload[position + 1 : position + 7].decode(
                            "ascii", errors="replace"
                        ),
                    }
                )
                position += 11
            return symbols

    return [
        {"market": None, "code": match.group(1).decode("ascii")}
        for match in SIX_DIGIT_CODE.finditer(payload[2:])
    ]


def _read_price(data: bytes, position: int) -> tuple[int, int]:
    if position >= len(data):
        raise CaptureDecodeError("compressed price starts beyond the response")

    value = data[position]
    result = value & 0x3F
    negative = bool(value & 0x40)
    shift = 6
    while value & 0x80:
        position += 1
        if position >= len(data):
            raise CaptureDecodeError("compressed price is truncated")
        value = data[position]
        result += (value & 0x7F) << shift
        shift += 7

    position += 1
    return (-result if negative else result), position


def encode_price(value: int) -> bytes:
    negative = value < 0
    remaining = abs(value)
    encoded = bytearray([(remaining & 0x3F) | (0x40 if negative else 0)])
    remaining >>= 6
    while remaining:
        encoded[-1] |= 0x80
        encoded.append(remaining & 0x7F)
        remaining >>= 7
    return bytes(encoded)


def parse_encrypted_quotes(payload: bytes) -> list[dict[str, Any]]:
    if len(payload) < 2:
        raise CaptureDecodeError(
            f"0x0547 response is too short: expected a count, got {len(payload)} bytes"
        )

    count = int.from_bytes(payload[:2], "little")
    position = 2
    rows: list[dict[str, Any]] = []
    for row_index in range(count):
        if position + 9 > len(payload):
            raise CaptureDecodeError(
                f"0x0547 quote row {row_index} header is truncated"
            )

        market = payload[position]
        code = payload[position + 1 : position + 7].decode("ascii", errors="replace")
        active = int.from_bytes(payload[position + 7 : position + 9], "little")
        position += 9

        close_raw, position = _read_price(payload, position)
        pre_close_diff, position = _read_price(payload, position)
        open_diff, position = _read_price(payload, position)
        high_diff, position = _read_price(payload, position)
        low_diff, position = _read_price(payload, position)

        if position + 4 > len(payload):
            raise CaptureDecodeError(f"0x0547 quote row {row_index} time is truncated")
        time_raw = int.from_bytes(payload[position : position + 4], "little")
        position += 4

        unknown_price, position = _read_price(payload, position)
        volume, position = _read_price(payload, position)
        current_volume, position = _read_price(payload, position)
        if position + 4 > len(payload):
            raise CaptureDecodeError(
                f"0x0547 quote row {row_index} amount is truncated"
            )
        (amount,) = struct.unpack_from("<f", payload, position)
        position += 4

        in_volume, position = _read_price(payload, position)
        out_volume, position = _read_price(payload, position)
        sell_amount, position = _read_price(payload, position)
        open_amount, position = _read_price(payload, position)

        levels = []
        for level in range(1, 6):
            bid_diff, position = _read_price(payload, position)
            ask_diff, position = _read_price(payload, position)
            bid_volume, position = _read_price(payload, position)
            ask_volume, position = _read_price(payload, position)
            levels.append(
                {
                    "level": level,
                    "bid": (close_raw + bid_diff) / 100,
                    "ask": (close_raw + ask_diff) / 100,
                    "bid_volume": bid_volume,
                    "ask_volume": ask_volume,
                }
            )

        if position + 10 > len(payload):
            raise CaptureDecodeError(f"0x0547 quote row {row_index} tail is truncated")
        tail = payload[position : position + 10]
        position += 10

        extra_values = []
        for _ in range(24):
            value, position = _read_price(payload, position)
            extra_values.append(value)

        rows.append(
            {
                "market": market,
                "code": code,
                "active": active,
                "close": close_raw / 100,
                "pre_close": (close_raw + pre_close_diff) / 100,
                "open": (close_raw + open_diff) / 100,
                "high": (close_raw + high_diff) / 100,
                "low": (close_raw + low_diff) / 100,
                "server_time": (
                    f"{time_raw // 10000:02d}:"
                    f"{(time_raw // 100) % 100:02d}:"
                    f"{time_raw % 100:02d}"
                ),
                "unknown_price": unknown_price / 100,
                "volume": volume,
                "current_volume": current_volume,
                "amount": amount,
                "in_volume": in_volume,
                "out_volume": out_volume,
                "sell_amount": sell_amount,
                "open_amount": open_amount,
                "levels": levels,
                "tail_hex": tail.hex(),
                "extra_values": extra_values,
            }
        )

    if position != len(payload):
        raise CaptureDecodeError(
            f"0x0547 response has {len(payload) - position} unconsumed bytes"
        )
    return rows


def load_event_spans(path: Path) -> list[EventSpan]:
    if not path.exists():
        return []

    spans = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CaptureDecodeError(
                f"invalid JSON in {path} at line {line_number}"
            ) from exc
        if event.get("kind") != "data":
            continue
        spans.append(
            EventSpan(
                direction=str(event["direction"]),
                offset=int(event["offset"]),
                length=int(event["length"]),
                timestamp=str(event["timestamp"]),
            )
        )
    return spans


def _timestamp_for_offset(
    spans: list[EventSpan], direction: str, offset: int
) -> str | None:
    for span in spans:
        if (
            span.direction == direction
            and span.offset <= offset < span.offset + span.length
        ):
            return span.timestamp
    return None


def _find_all(data: bytes, needle: bytes) -> list[int]:
    if not needle:
        return []
    offsets = []
    position = 0
    while True:
        position = data.find(needle, position)
        if position < 0:
            return offsets
        offsets.append(position)
        position += 1


def find_price_encodings(
    payload: bytes,
    price: Decimal,
    *,
    bases: dict[str, Decimal] | None = None,
) -> list[dict[str, object]]:
    cents_decimal = price * 100
    if cents_decimal != cents_decimal.to_integral_value():
        raise ValueError(f"price must have at most two decimal places: {price}")
    cents = int(cents_decimal)

    candidates: list[tuple[str, bytes]] = [
        ("ascii-2dp", f"{price:.2f}".encode("ascii")),
        ("ascii-compact", format(price.normalize(), "f").encode("ascii")),
        ("float32-le", struct.pack("<f", float(price))),
        ("float64-le", struct.pack("<d", float(price))),
        ("tdx-price-absolute", encode_price(cents)),
    ]
    if 0 <= cents <= 0xFFFF:
        candidates.append(("uint16-cents-le", struct.pack("<H", cents)))
    if 0 <= cents <= 0xFFFFFFFF:
        candidates.append(("uint32-cents-le", struct.pack("<I", cents)))
    for name, base in (bases or {}).items():
        base_cents_decimal = base * 100
        if base_cents_decimal == base_cents_decimal.to_integral_value():
            candidates.append(
                (
                    f"tdx-price-delta-{name}",
                    encode_price(cents - int(base_cents_decimal)),
                )
            )

    matches = []
    seen: set[tuple[bytes, tuple[int, ...]]] = set()
    for encoding, needle in candidates:
        offsets = _find_all(payload, needle)
        key = needle, tuple(offsets)
        if not offsets or key in seen:
            continue
        seen.add(key)
        matches.append(
            {
                "encoding": encoding,
                "bytes_hex": needle.hex(),
                "offsets": offsets,
            }
        )
    return matches


def analyze_session(
    session_dir: str | Path,
    *,
    commands: set[int] | None = None,
    codes: set[str] | None = None,
    search_prices: list[Decimal] | None = None,
) -> dict[str, object]:
    directory = Path(session_dir)
    request_frames = parse_request_stream(
        (directory / "client-to-server.bin").read_bytes()
    )
    response_frames = parse_response_stream(
        (directory / "server-to-client.bin").read_bytes()
    )
    if len(request_frames) != len(response_frames):
        raise CaptureDecodeError(
            f"request/response frame count differs: "
            f"{len(request_frames)} != {len(response_frames)}"
        )

    spans = load_event_spans(directory / "events.jsonl")
    results = []
    for request, response in zip(request_frames, response_frames, strict=True):
        command = request.command
        symbols = extract_request_symbols(request)
        symbol_codes = {str(symbol["code"]) for symbol in symbols}
        if commands is not None and command not in commands:
            continue
        if codes is not None and not codes.intersection(symbol_codes):
            continue

        decoded_payload = decode_command_payload(command, response.payload)
        item: dict[str, object] = {
            "index": request.index,
            "command": command,
            "command_hex": f"0x{command:04X}" if command is not None else None,
            "symbols": symbols,
            "request": {
                "offset": request.offset,
                "raw_length": request.raw_length,
                "payload_length": len(request.payload),
                "timestamp": _timestamp_for_offset(
                    spans, "client_to_server", request.offset
                ),
            },
            "response": {
                "offset": response.offset,
                "raw_length": response.raw_length,
                "compressed_size": response.compressed_size,
                "uncompressed_size": response.uncompressed_size,
                "timestamp": _timestamp_for_offset(
                    spans, "server_to_client", response.offset
                ),
                "xor_key": (
                    f"0x{COMMAND_XOR_KEYS[command]:02X}"
                    if command in COMMAND_XOR_KEYS
                    else None
                ),
                "decoded_preview_hex": decoded_payload[:64].hex(),
            },
        }

        quotes = None
        if command == 0x0547:
            quotes = parse_encrypted_quotes(decoded_payload)
            item["quotes"] = quotes

        if search_prices:
            bases: dict[str, Decimal] = {}
            if quotes and len(quotes) == 1:
                bases = {
                    "close": Decimal(str(quotes[0]["close"])),
                    "pre-close": Decimal(str(quotes[0]["pre_close"])),
                }
            item["price_search"] = [
                {
                    "price": str(price),
                    "matches": find_price_encodings(
                        decoded_payload, price, bases=bases
                    ),
                }
                for price in search_prices
            ]

        results.append(item)

    return {
        "session_dir": str(directory.resolve()),
        "request_frame_count": len(request_frames),
        "response_frame_count": len(response_frames),
        "matched_frame_count": len(results),
        "frames": results,
    }
