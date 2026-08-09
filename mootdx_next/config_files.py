from __future__ import annotations

import hashlib
import struct
import threading
import time
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from types import MappingProxyType
from zipfile import BadZipFile
from zipfile import ZipFile

from mootdx_next.errors import ConfigArchiveError
from mootdx_next.errors import ConfigFileError

ZHB_FILENAME = "zhb.zip"
INFOHARBOR_BLOCK_FILENAME = "infoharbor_block.dat"
TDX_BASE_FILENAME = "base.dbf"
TDX_ZS_FILENAME = "tdxzs.cfg"
TDX_ZS3_FILENAME = "tdxzs3.cfg"
TDX_ZS_BASE_FILENAME = "tdxzsbase.cfg"
TDX_BK_FILENAME = "tdxbk.cfg"
TDX_STAT_FILENAME = "tdxstat.cfg"
TDX_STAT2_FILENAME = "tdxstat2.cfg"
XGSG_FILENAME = "xgsg.cfg"
SP_BLOCK_FILENAME = "spblock.dat"
TDX_HY_FILENAME = "tdxhy.cfg"

ZHB_CACHE_TTL_SECONDS = 10 * 60
ZHB_MAX_MEMBERS = 256
ZHB_MAX_MEMBER_SIZE = 32 * 1024 * 1024
ZHB_MAX_UNCOMPRESSED_SIZE = 128 * 1024 * 1024

ZhbLoader = Callable[[], bytes]

TDX_BLOCK_TYPE_CATEGORIES = {
    2: ("industry", "行业板块", "tdx"),
    3: ("region", "地区板块", None),
    4: ("concept", "概念板块", None),
    5: ("style", "风格板块", None),
    12: ("industry", "行业板块", "sw"),
}
INFOHARBOR_BLOCK_CATEGORIES = {
    "GN": ("concept", "概念板块"),
    "FG": ("style", "风格板块"),
    "ZS": ("index", "指数板块"),
}


@dataclass(frozen=True, slots=True)
class ZhbSnapshot:
    files: Mapping[str, bytes]
    sha256: str
    loaded_at: float


class ZhbRegistry:
    """Process-safe immutable snapshot cache for the TDX ``zhb.zip`` bundle."""

    def __init__(
        self,
        ttl_seconds: float = ZHB_CACHE_TTL_SECONDS,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        self._ttl_seconds = float(ttl_seconds)
        self._time_fn = time_fn
        self._condition = threading.Condition()
        self._snapshot: ZhbSnapshot | None = None
        self._expires_at = 0.0
        self._refreshing = False

    @property
    def ttl_seconds(self) -> float:
        return self._ttl_seconds

    def get(self, loader: ZhbLoader, *, refresh: bool = False) -> ZhbSnapshot:
        with self._condition:
            if self._refreshing:
                self._condition.wait_for(lambda: not self._refreshing)
                if self._snapshot is not None:
                    return self._snapshot

            if not refresh and self._snapshot is not None and self._time_fn() < self._expires_at:
                return self._snapshot
            self._refreshing = True

        try:
            payload = bytes(loader())
            files = unpack_zhb(payload)
            snapshot = ZhbSnapshot(
                files=files,
                sha256=hashlib.sha256(payload).hexdigest(),
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

    def snapshot(self) -> ZhbSnapshot | None:
        with self._condition:
            return self._snapshot


def unpack_zhb(payload: bytes) -> Mapping[str, bytes]:
    if not payload:
        raise ConfigArchiveError("zhb.zip is empty")

    try:
        archive = ZipFile(BytesIO(payload))
    except BadZipFile as exc:
        raise ConfigArchiveError("zhb.zip is not a valid ZIP archive") from exc

    with archive:
        members = archive.infolist()
        if len(members) > ZHB_MAX_MEMBERS:
            raise ConfigArchiveError(
                f"zhb.zip has too many members: {len(members)} > {ZHB_MAX_MEMBERS}"
            )

        total_size = 0
        files: dict[str, bytes] = {}
        seen: set[str] = set()
        for member in members:
            if member.is_dir():
                continue
            normalized = member.filename.replace("\\", "/")
            path = PurePosixPath(normalized)
            if path.is_absolute() or ".." in path.parts or not normalized:
                raise ConfigArchiveError(f"unsafe zhb.zip member path: {member.filename!r}")
            if member.flag_bits & 0x1:
                raise ConfigArchiveError(f"encrypted zhb.zip member is not supported: {normalized}")
            if member.file_size > ZHB_MAX_MEMBER_SIZE:
                raise ConfigArchiveError(
                    f"zhb.zip member is too large: {normalized} ({member.file_size} bytes)"
                )

            total_size += member.file_size
            if total_size > ZHB_MAX_UNCOMPRESSED_SIZE:
                raise ConfigArchiveError(
                    f"zhb.zip expands beyond {ZHB_MAX_UNCOMPRESSED_SIZE} bytes"
                )

            key = normalized.casefold()
            if key in seen:
                raise ConfigArchiveError(f"duplicate zhb.zip member: {normalized}")
            seen.add(key)
            try:
                content = archive.read(member)
            except (BadZipFile, RuntimeError) as exc:
                raise ConfigArchiveError(f"failed to read zhb.zip member: {normalized}") from exc
            if len(content) != member.file_size:
                raise ConfigArchiveError(
                    f"zhb.zip member size mismatch for {normalized}: "
                    f"expected {member.file_size}, got {len(content)}"
                )
            files[normalized] = bytes(content)

    return MappingProxyType(files)


def get_zhb_file(files: Mapping[str, bytes], filename: str) -> bytes:
    wanted = filename.casefold()
    for name, content in files.items():
        if name.casefold() == wanted:
            return content
    raise ConfigFileError(f"{ZHB_FILENAME} does not contain {filename}")


def parse_tdx_block_indexes(data: bytes) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for fields in _records(data):
        if len(fields) < 2 or not fields[0] or not fields[1]:
            continue
        block_type = _to_int(_field(fields, 2))
        category, category_name, taxonomy = TDX_BLOCK_TYPE_CATEGORIES.get(
            block_type,
            (None, None, None),
        )
        rows.append(
            {
                "name": fields[0],
                "code": fields[1],
                "type": block_type,
                "subtype": _to_int(_field(fields, 3)),
                "reference": _field(fields, 5),
                "category": category,
                "category_name": category_name,
                "taxonomy": taxonomy,
            }
        )
    return rows


def parse_tdx_block_base(data: bytes) -> list[dict[str, object]]:
    """Parse the live block-index base file used by TDX ratio fields.

    Share and market-value columns in the source file are published in units
    of ten thousand. The normalized columns below use shares and yuan so
    callers can combine them directly with amount fields from the quote wire.
    """

    rows: list[dict[str, object]] = []
    for fields in _records(data):
        if len(fields) < 8 or not fields[1]:
            continue
        market = _to_int(fields[0])
        total_shares = _to_float(_field(fields, 2))
        circulating_shares = _to_float(_field(fields, 3))
        total_market_cap = _to_float(_field(fields, 4))
        circulating_market_cap = _to_float(_field(fields, 5))
        if market is None:
            continue
        rows.append(
            {
                "market": market,
                "code": fields[1],
                "total_shares": None if total_shares is None else total_shares * 10_000.0,
                "circulating_shares": None if circulating_shares is None else circulating_shares * 10_000.0,
                "total_market_cap": None if total_market_cap is None else total_market_cap * 10_000.0,
                "circulating_market_cap": (
                    None if circulating_market_cap is None else circulating_market_cap * 10_000.0
                ),
                "date": _field(fields, 7),
                "raw_fields": tuple(fields),
            }
        )
    return rows


def parse_tdx_block_aliases(data: bytes) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for fields in _records(data):
        if len(fields) < 3 or not fields[1] or not fields[2]:
            continue
        rows.append({"short_name": fields[1], "full_name": fields[2]})
    return rows


def parse_infoharbor_blocks(data: bytes) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    current_members: list[dict[str, object]] = []

    def append_current() -> None:
        if current is None:
            return
        item = dict(current)
        item["actual_count"] = len(current_members)
        item["members"] = tuple(dict(member) for member in current_members)
        blocks.append(item)

    for raw_line in _decode_lines(data):
        line = raw_line.strip().strip("\x00")
        if not line:
            continue
        if line.startswith("#"):
            append_current()
            current = None
            current_members = []

            fields = line[1:].split(",")
            header = fields[0]
            if "_" not in header:
                continue
            prefix, name = header.split("_", 1)
            category = INFOHARBOR_BLOCK_CATEGORIES.get(prefix)
            if category is None or not name:
                continue
            current = {
                "name": name,
                "code": _field(fields, 2),
                "category": category[0],
                "category_name": category[1],
                "declared_count": _to_int(_field(fields, 1)),
                "created_date": _field(fields, 3),
                "updated_date": _field(fields, 4),
            }
            continue

        if current is None:
            continue
        for token in line.split(","):
            market_value, separator, code = token.strip().partition("#")
            market = _to_int(market_value)
            if separator and market is not None and code:
                current_members.append({"market": market, "code": code})

    append_current()
    return blocks


def parse_tdx_base_finance(data: bytes) -> list[dict[str, object]]:
    if len(data) < 33:
        raise ConfigFileError(f"{TDX_BASE_FILENAME} header is truncated")

    try:
        record_count = struct.unpack_from("<I", data, 4)[0]
        header_length, record_length = struct.unpack_from("<HH", data, 8)
    except struct.error as exc:
        raise ConfigFileError(f"failed to decode {TDX_BASE_FILENAME} header") from exc
    if header_length < 33 or record_length <= 1 or header_length > len(data):
        raise ConfigFileError(
            f"invalid {TDX_BASE_FILENAME} dimensions: header={header_length}, record={record_length}"
        )

    fields: dict[str, tuple[int, int]] = {}
    descriptor_pos = 32
    field_offset = 1
    while descriptor_pos < header_length and data[descriptor_pos] != 0x0D:
        descriptor = data[descriptor_pos : descriptor_pos + 32]
        if len(descriptor) != 32:
            raise ConfigFileError(f"{TDX_BASE_FILENAME} field descriptor is truncated")
        name = descriptor[:11].split(b"\x00", 1)[0].decode("ascii", "ignore").upper()
        length = descriptor[16]
        if not name or length <= 0 or field_offset + length > record_length:
            raise ConfigFileError(f"invalid {TDX_BASE_FILENAME} field descriptor at {descriptor_pos}")
        fields[name] = (field_offset, length)
        field_offset += length
        descriptor_pos += 32
    if descriptor_pos >= header_length or data[descriptor_pos] != 0x0D:
        raise ConfigFileError(f"{TDX_BASE_FILENAME} field descriptors have no terminator")

    required = {"SC", "GPDM", "DY"}
    missing = sorted(required - fields.keys())
    if missing:
        raise ConfigFileError(f"{TDX_BASE_FILENAME} is missing fields: {', '.join(missing)}")
    records_end = header_length + record_count * record_length
    if records_end > len(data):
        raise ConfigFileError(
            f"{TDX_BASE_FILENAME} records are truncated: expected {records_end} bytes, got {len(data)}"
        )

    def read_field(record: bytes, name: str) -> str:
        location = fields.get(name)
        if location is None:
            return ""
        offset, length = location
        return record[offset : offset + length].decode("ascii", "ignore").strip()

    rows: list[dict[str, object]] = []
    for index in range(record_count):
        start = header_length + index * record_length
        record = data[start : start + record_length]
        if record[:1] == b"*":
            continue
        market = _to_int(read_field(record, "SC"))
        code = read_field(record, "GPDM")
        if market is None or not code:
            continue
        rows.append(
            {
                "market": market,
                "code": code,
                "province": _to_int(read_field(record, "DY")),
                "industry": _to_int(read_field(record, "HY")),
                "updated_date": read_field(record, "GXRQ"),
            }
        )
    return rows


def parse_sp_blocks(data: bytes) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = []
    current_name: str | None = None
    current_codes: list[str] = []

    def append_current() -> None:
        if current_name is not None:
            blocks.append(
                {
                    "blockname": current_name,
                    "count": len(current_codes),
                    "codes": tuple(current_codes),
                }
            )

    for line in _decode_lines(data):
        value = line.strip("\x00")
        if not value:
            continue
        if value.startswith("#"):
            append_current()
            current_name = value[1:].strip()
            current_codes = []
            continue
        if current_name is not None and len(value) == 7 and value.isdigit():
            current_codes.append(value)
    append_current()
    return blocks


def parse_tdx_industries(data: bytes) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for fields in _records(data):
        if len(fields) < 3 or not fields[0] or not fields[1]:
            continue
        market = _to_int(fields[0])
        if market is None:
            continue
        rows.append(
            {
                "market": market,
                "code": fields[1],
                "tdx_industry": fields[2],
                "sw_industry": _field(fields, 5) or _field(fields, len(fields) - 1),
            }
        )
    return rows


def parse_ipo_subscriptions(data: bytes) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for fields in _records(data):
        if len(fields) < 15 or not fields[1]:
            continue
        market = _to_int(fields[0])
        if market is None:
            continue
        rows.append(
            {
                "market": market,
                "code": fields[1],
                "subscription_date": _field(fields, 2),
                "issue_price": _to_float(_field(fields, 3)),
                "name": _field(fields, 14),
                "raw_fields": tuple(fields),
            }
        )
    return rows


def parse_stock_statistics(data: bytes) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for fields in _records(data):
        if len(fields) < 5 or not fields[1]:
            continue
        market = _to_int(fields[0])
        if market is None:
            continue
        rows.append(
            {
                "market": market,
                "code": fields[1],
                "date": _field(fields, 4),
                "pe_ttm": _to_float(_field(fields, 3)),
                "trend_days": _to_int(_field(fields, 5)),
                "change_pct": _to_float(_field(fields, 6)),
                "pe_static": _to_float(_field(fields, 9)),
                "dividend_yield": _to_float(_field(fields, 10)),
                "change_5d": _to_float(_field(fields, 28)),
                "change_10d": _to_float(_field(fields, 30)),
                "change_20d": _to_float(_field(fields, 18)),
                "change_60d": _to_float(_field(fields, 20)),
                "change_ytd": _to_float(_field(fields, 21)),
                "raw_fields": tuple(fields),
            }
        )
    return rows


def parse_stock_statistics2(data: bytes) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for fields in _records(data):
        if len(fields) < 14 or not fields[1]:
            continue
        market = _to_int(fields[0])
        if market is None:
            continue
        rows.append(
            {
                "market": market,
                "code": fields[1],
                "date": _field(fields, 2),
                "amount": _to_float(_field(fields, 3)),
                "previous_amount": _to_float(_field(fields, 5)),
                "block_index": _field(fields, 13),
                "ipo_price": _to_float(_field(fields, 16)),
                "high_52w": _to_float(_field(fields, 17)),
                "low_52w": _to_float(_field(fields, 18)),
                "raw_fields": tuple(fields),
            }
        )
    return rows


def _records(data: bytes) -> list[list[str]]:
    records: list[list[str]] = []
    for line in _decode_lines(data):
        if not line or line.startswith("#"):
            continue
        records.append(line.split("|"))
    return records


def _decode_lines(data: bytes) -> list[str]:
    return [line.rstrip("\r") for line in data.decode("gbk", "ignore").splitlines()]


def _field(fields: list[str], index: int) -> str:
    return fields[index].strip() if 0 <= index < len(fields) else ""


def _to_int(value: str) -> int | None:
    try:
        return int(value.strip())
    except (AttributeError, TypeError, ValueError):
        return None


def _to_float(value: str) -> float | None:
    try:
        return float(value.strip())
    except (AttributeError, TypeError, ValueError):
        return None


zhb_registry = ZhbRegistry()


def invalidate_zhb_cache() -> None:
    zhb_registry.invalidate()


def zhb_snapshot() -> ZhbSnapshot | None:
    return zhb_registry.snapshot()
