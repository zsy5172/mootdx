from __future__ import annotations

import base64
import hashlib
import math
import struct
import threading
import time
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Protocol
from zipfile import BadZipFile
from zipfile import ZipFile

import httpx

from mootdx_next.errors import GbbqArchiveError
from mootdx_next.errors import GbbqDecodeError
from mootdx_next.errors import GbbqDownloadError
from mootdx_next.protocol.std_quotes import XDXR_CATEGORY_MAPPING

GBBQ_URL = "https://www.tdx.com.cn/products/data/data/dbf/gbbq.zip"
GBBQ_MEMBER_NAME = "gbbq"
GBBQ_CACHE_TTL_SECONDS = 10 * 60
GBBQ_REQUEST_TIMEOUT_SECONDS = 30.0
GBBQ_MAX_DOWNLOAD_SIZE = 32 * 1024 * 1024
GBBQ_MAX_MEMBERS = 128
GBBQ_MAX_MEMBER_SIZE = 32 * 1024 * 1024
GBBQ_MAX_UNCOMPRESSED_SIZE = 64 * 1024 * 1024
GBBQ_MAX_COMPRESSION_RATIO = 100.0
GBBQ_RECORD_SIZE = 29

# The official ``gbbq`` file encrypts the first 24 bytes of each record with
# a fixed TDX lookup table.  The table is encoded only to keep the source
# compact; it is protocol material, not a credential or runtime secret.
_DECRYPTION_TABLE_B64 = "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"
_DECRYPTION_TABLE = base64.b64decode(_DECRYPTION_TABLE_B64, validate=True)
if len(_DECRYPTION_TABLE) != 4176:  # pragma: no cover - import-time invariant
    raise RuntimeError("invalid embedded GBBQ decryption table")
_DECRYPTION_WORDS = struct.unpack("<1044I", _DECRYPTION_TABLE)

_U32 = struct.Struct("<I")
_FLOAT4 = struct.Struct("<ffff")
GbbqLoader = Callable[[], bytes]


class GbbqProvider(Protocol):
    def load(self) -> bytes: ...


@dataclass(frozen=True, slots=True)
class GbbqEvent:
    market: int
    code: str
    date: str
    category: int
    c1: float
    c2: float
    c3: float
    c4: float

    @property
    def symbol(self) -> str:
        prefix = {0: "sz", 1: "sh", 2: "bj"}.get(self.market, f"m{self.market}")
        return f"{prefix}{self.code}"

    def to_dict(self) -> dict[str, object]:
        row: dict[str, object] = {
            "market": self.market,
            "code": self.code,
            "symbol": self.symbol,
            "date": self.date,
            "category": self.category,
            "name": XDXR_CATEGORY_MAPPING.get(self.category, str(self.category)),
            "raw_c1": self.c1,
            "raw_c2": self.c2,
            "raw_c3": self.c3,
            "raw_c4": self.c4,
            "c1": self.c1,
            "c2": self.c2,
            "c3": self.c3,
            "c4": self.c4,
            "source": "gbbq.zip",
        }
        if self.category == 1:
            row.update(
                fenhong=self.c1,
                peigujia=self.c2,
                songzhuangu=self.c3,
                peigu=self.c4,
            )
        elif self.category in {11, 12}:
            row["suogu"] = self.c3
        elif self.category in {13, 14}:
            row.update(xingquanjia=self.c1, fenshu=self.c3)
        else:
            row.update(
                panqianliutong_wan_shares=self.c1,
                qianzongguben_wan_shares=self.c2,
                panhouliutong_wan_shares=self.c3,
                houzongguben_wan_shares=self.c4,
                panqianliutong_shares=self.c1 * 10000,
                qianzongguben_shares=self.c2 * 10000,
                panhouliutong_shares=self.c3 * 10000,
                houzongguben_shares=self.c4 * 10000,
            )
        return row


GbbqIndex = Mapping[tuple[int, str], tuple[GbbqEvent, ...]]


@dataclass(frozen=True, slots=True)
class GbbqSnapshot:
    events: tuple[GbbqEvent, ...]
    by_security: GbbqIndex
    sha256: str
    loaded_at: float

    def find(self, market: int, code: str) -> tuple[GbbqEvent, ...]:
        return self.by_security.get((int(market), str(code)), ())


class GbbqHttpProvider:
    def __init__(
        self,
        *,
        url: str = GBBQ_URL,
        timeout: float = GBBQ_REQUEST_TIMEOUT_SECONDS,
        max_bytes: int = GBBQ_MAX_DOWNLOAD_SIZE,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be greater than zero")
        self._url = str(url)
        self._timeout = float(timeout)
        self._max_bytes = int(max_bytes)
        self._client_factory = client_factory or self._new_client

    def load(self) -> bytes:
        try:
            with self._client_factory() as client:
                with client.stream("GET", self._url, timeout=self._timeout) as response:
                    response.raise_for_status()
                    content_length = response.headers.get("Content-Length")
                    if content_length is not None and int(content_length) > self._max_bytes:
                        raise GbbqDownloadError(
                            f"gbbq.zip exceeds {self._max_bytes} bytes"
                        )
                    payload = bytearray()
                    for chunk in response.iter_bytes():
                        payload.extend(chunk)
                        if len(payload) > self._max_bytes:
                            raise GbbqDownloadError(
                                f"gbbq.zip exceeds {self._max_bytes} bytes"
                            )
        except GbbqDownloadError:
            raise
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            raise GbbqDownloadError(f"failed to download gbbq.zip: {exc}") from exc
        if not payload:
            raise GbbqDownloadError("gbbq.zip is empty")
        return bytes(payload)

    @staticmethod
    def _new_client() -> httpx.Client:
        return httpx.Client(follow_redirects=False)


class GbbqRegistry:
    """Thread-safe process cache for an immutable all-market GBBQ snapshot."""

    def __init__(
        self,
        *,
        ttl_seconds: float = GBBQ_CACHE_TTL_SECONDS,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        self._ttl_seconds = float(ttl_seconds)
        self._time_fn = time_fn
        self._condition = threading.Condition()
        self._snapshot: GbbqSnapshot | None = None
        self._expires_at = 0.0
        self._refreshing = False

    @property
    def ttl_seconds(self) -> float:
        return self._ttl_seconds

    def get(self, loader: GbbqLoader, *, refresh: bool = False) -> GbbqSnapshot:
        with self._condition:
            if self._refreshing:
                self._condition.wait_for(lambda: not self._refreshing)
                if self._snapshot is not None:
                    return self._snapshot
            if not refresh and self._snapshot is not None and self._time_fn() < self._expires_at:
                return self._snapshot
            self._refreshing = True

        try:
            archive = bytes(loader())
            encrypted = extract_gbbq_member(archive)
            events = decode_gbbq(encrypted)
            grouped: dict[tuple[int, str], list[GbbqEvent]] = {}
            for event in events:
                grouped.setdefault((event.market, event.code), []).append(event)
            index = MappingProxyType(
                {
                    key: tuple(sorted(values, key=lambda event: (event.date, event.category)))
                    for key, values in grouped.items()
                }
            )
            snapshot = GbbqSnapshot(
                events=events,
                by_security=index,
                sha256=hashlib.sha256(archive).hexdigest(),
                loaded_at=self._time_fn(),
            )
        except BaseException:
            with self._condition:
                self._refreshing = False
                self._condition.notify_all()
            raise

        with self._condition:
            self._snapshot = snapshot
            self._expires_at = self._time_fn() + self._ttl_seconds
            self._refreshing = False
            self._condition.notify_all()
            return snapshot

    def invalidate(self) -> None:
        with self._condition:
            self._expires_at = 0.0

    def snapshot(self) -> GbbqSnapshot | None:
        with self._condition:
            return self._snapshot


def extract_gbbq_member(payload: bytes) -> bytes:
    if not payload:
        raise GbbqArchiveError("gbbq.zip is empty")
    if len(payload) > GBBQ_MAX_DOWNLOAD_SIZE:
        raise GbbqArchiveError(f"gbbq.zip exceeds {GBBQ_MAX_DOWNLOAD_SIZE} bytes")
    try:
        archive = ZipFile(BytesIO(payload))
    except BadZipFile as exc:
        raise GbbqArchiveError("gbbq.zip is not a valid ZIP archive") from exc

    with archive:
        members = archive.infolist()
        if len(members) > GBBQ_MAX_MEMBERS:
            raise GbbqArchiveError(
                f"gbbq.zip has too many members: {len(members)} > {GBBQ_MAX_MEMBERS}"
            )
        total_size = 0
        target = None
        seen: set[str] = set()
        for member in members:
            normalized = member.filename.replace("\\", "/")
            path = PurePosixPath(normalized)
            if path.is_absolute() or ".." in path.parts or not normalized:
                raise GbbqArchiveError(f"unsafe gbbq.zip member path: {member.filename!r}")
            key = normalized.casefold()
            if key in seen:
                raise GbbqArchiveError(f"duplicate gbbq.zip member: {normalized}")
            seen.add(key)
            if member.is_dir():
                continue
            if member.flag_bits & 0x1:
                raise GbbqArchiveError(f"encrypted ZIP member is not supported: {normalized}")
            if member.file_size > GBBQ_MAX_MEMBER_SIZE:
                raise GbbqArchiveError(f"gbbq.zip member is too large: {normalized}")
            total_size += member.file_size
            if total_size > GBBQ_MAX_UNCOMPRESSED_SIZE:
                raise GbbqArchiveError(
                    f"gbbq.zip expands beyond {GBBQ_MAX_UNCOMPRESSED_SIZE} bytes"
                )
            ratio = member.file_size / max(member.compress_size, 1)
            if ratio > GBBQ_MAX_COMPRESSION_RATIO:
                raise GbbqArchiveError(
                    f"gbbq.zip member compression ratio is unsafe: {normalized} ({ratio:.1f})"
                )
            if path.name.casefold() == GBBQ_MEMBER_NAME:
                if target is not None:
                    raise GbbqArchiveError("gbbq.zip contains multiple gbbq members")
                target = member
        if target is None:
            raise GbbqArchiveError("gbbq.zip does not contain the gbbq member")
        try:
            content = archive.read(target)
        except (BadZipFile, RuntimeError) as exc:
            raise GbbqArchiveError("failed to read the gbbq member") from exc
        if len(content) != target.file_size:
            raise GbbqArchiveError(
                f"gbbq member size mismatch: expected {target.file_size}, got {len(content)}"
            )
        return bytes(content)


def decode_gbbq(content: bytes) -> tuple[GbbqEvent, ...]:
    if len(content) < _U32.size:
        raise GbbqDecodeError(f"gbbq content is too short: {len(content)}")
    (count,) = _U32.unpack_from(content)
    expected_size = _U32.size + count * GBBQ_RECORD_SIZE
    if len(content) != expected_size:
        raise GbbqDecodeError(
            f"invalid gbbq size: expected {expected_size} bytes for {count} records, got {len(content)}"
        )

    events: list[GbbqEvent] = []
    pos = _U32.size
    for index in range(count):
        record = content[pos : pos + GBBQ_RECORD_SIZE]
        pos += GBBQ_RECORD_SIZE
        clear = bytearray()
        for block_pos in (0, 8, 16):
            left, right = _decrypt_block(record, block_pos)
            clear.extend(struct.pack("<II", left, right))
        clear.extend(record[24:29])

        market = clear[0]
        raw_code = bytes(clear[1:8]).split(b"\x00", 1)[0]
        try:
            code = raw_code.decode("ascii")
        except UnicodeDecodeError as exc:
            raise GbbqDecodeError(f"gbbq record {index} has a non-ASCII code") from exc
        if len(code) != 6 or not code.isdigit():
            raise GbbqDecodeError(f"gbbq record {index} has invalid code {code!r}")

        (date_value,) = _U32.unpack_from(clear, 8)
        year, remainder = divmod(date_value, 10000)
        month, day_value = divmod(remainder, 100)
        try:
            event_date = date(year, month, day_value)
        except ValueError as exc:
            raise GbbqDecodeError(
                f"gbbq record {index} has invalid date {date_value:08d}"
            ) from exc
        category = clear[12]
        values = _FLOAT4.unpack_from(clear, 13)
        if any(not math.isfinite(value) for value in values):
            raise GbbqDecodeError(f"gbbq record {index} contains a non-finite value")
        events.append(
            GbbqEvent(
                market=market,
                code=code,
                date=event_date.isoformat(),
                category=category,
                c1=float(values[0]),
                c2=float(values[1]),
                c3=float(values[2]),
                c4=float(values[3]),
            )
        )
    return tuple(events)


def _decrypt_block(record: bytes, offset: int) -> tuple[int, int]:
    left, right = struct.unpack_from("<II", record, offset)
    number = _DECRYPTION_WORDS[0x44 // 4] ^ left
    previous = right
    for key_index in range(0x40 // 4, 0, -1):
        value = _DECRYPTION_WORDS[0x448 // 4 + ((number >> 16) & 0xFF)]
        value = (value + _DECRYPTION_WORDS[0x48 // 4 + (number >> 24)]) & 0xFFFFFFFF
        value ^= _DECRYPTION_WORDS[0x848 // 4 + ((number >> 8) & 0xFF)]
        value = (value + _DECRYPTION_WORDS[0xC48 // 4 + (number & 0xFF)]) & 0xFFFFFFFF
        value ^= _DECRYPTION_WORDS[key_index]
        number, previous = (previous ^ value) & 0xFFFFFFFF, number
    previous ^= _DECRYPTION_WORDS[0]
    return previous, number


gbbq_registry = GbbqRegistry()
gbbq_provider = GbbqHttpProvider()


def invalidate_gbbq_cache() -> None:
    gbbq_registry.invalidate()


def gbbq_snapshot() -> GbbqSnapshot | None:
    return gbbq_registry.snapshot()


__all__ = [
    "decode_gbbq",
    "extract_gbbq_member",
    "gbbq_provider",
    "gbbq_registry",
    "gbbq_snapshot",
    "GbbqEvent",
    "GbbqHttpProvider",
    "GbbqProvider",
    "GbbqRegistry",
    "GbbqSnapshot",
    "invalidate_gbbq_cache",
]
