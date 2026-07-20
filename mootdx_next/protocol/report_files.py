from __future__ import annotations

import struct

from mootdx_next.errors import ProtocolDecodeError

REPORT_FILE_NODE_SIZE = 0x7530


def encode_report_file(filename: str, offset: int = 0, node_size: int = REPORT_FILE_NODE_SIZE) -> bytes:
    pkg = bytearray.fromhex("0C 12 34 00 00 00")
    raw_data = struct.pack("<H2I100s", 0x06B9, offset, node_size, filename.encode("utf-8"))
    raw_data_len = struct.calcsize("<H2I100s")
    pkg.extend(struct.pack(f"<HH{raw_data_len}s", raw_data_len, raw_data_len, raw_data))
    return bytes(pkg)


def decode_report_file_chunk(body: bytes) -> dict[str, bytes | int]:
    if len(body) < 4:
        raise ProtocolDecodeError("report file response body is too short")

    try:
        (chunk_size,) = struct.unpack("<I", body[:4])
    except struct.error as exc:
        raise ProtocolDecodeError("failed to decode report file chunk size") from exc

    chunk_data = body[4:]
    if chunk_size > len(chunk_data):
        raise ProtocolDecodeError(
            f"expected {chunk_size} report file bytes, got {len(chunk_data)}"
        )

    return {
        "chunksize": chunk_size,
        "chunkdata": chunk_data[:chunk_size],
    }


def decode_ex_instrument_count(body: bytes) -> int:
    if len(body) < 23:
        raise ProtocolDecodeError("ext instrument count response body is too short")

    try:
        (count,) = struct.unpack("<I", body[19:23])
    except struct.error as exc:
        raise ProtocolDecodeError("failed to decode ext instrument count response") from exc

    return count
