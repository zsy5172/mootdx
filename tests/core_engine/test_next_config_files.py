from __future__ import annotations

import struct
import threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from zipfile import ZipFile

import pytest

from mootdx_next.api.clients import SyncClient
from mootdx_next.config_files import parse_infoharbor_blocks
from mootdx_next.config_files import parse_ipo_subscriptions
from mootdx_next.config_files import parse_sp_blocks
from mootdx_next.config_files import parse_stock_statistics
from mootdx_next.config_files import parse_stock_statistics2
from mootdx_next.config_files import parse_tdx_block_aliases
from mootdx_next.config_files import parse_tdx_block_indexes
from mootdx_next.config_files import parse_tdx_base_finance
from mootdx_next.config_files import parse_tdx_industries
from mootdx_next.config_files import unpack_zhb
from mootdx_next.config_files import ZhbRegistry
from mootdx_next.errors import ConfigArchiveError


class Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def _zip(files: dict[str, bytes]) -> bytes:
    target = BytesIO()
    with ZipFile(target, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return target.getvalue()


def _base_dbf(rows: list[tuple[int, str, str, int, int]]) -> bytes:
    fields = (
        ("SC", "C", 1),
        ("GPDM", "C", 6),
        ("GXRQ", "N", 8),
        ("DY", "C", 3),
        ("HY", "C", 4),
    )
    header_length = 32 + len(fields) * 32 + 1
    record_length = 1 + sum(length for _, _, length in fields)
    header = bytearray(b"\x00" * header_length)
    header[0] = 0x03
    struct.pack_into("<IHH", header, 4, len(rows), header_length, record_length)
    for index, (name, kind, length) in enumerate(fields):
        descriptor = 32 + index * 32
        header[descriptor : descriptor + len(name)] = name.encode("ascii")
        header[descriptor + 11] = ord(kind)
        header[descriptor + 16] = length
    header[-1] = 0x0D

    records = bytearray()
    for market, code, updated_date, province, industry in rows:
        records.extend(
            (
                f" {market:<1}{code:<6}{updated_date:>8}{province:>3}{industry:>4}"
            ).encode("ascii")
        )
    return bytes(header + records + b"\x1a")


def _config_files() -> dict[str, bytes]:
    stat = [""] * 35
    stat[0:7] = ["0", "000001", "", "5.05", "20260728", "5", "0.81"]
    stat[9] = "5.0981"
    stat[10] = "5.32"
    stat[18] = "11.44"
    stat[20] = "0.36"
    stat[21] = "1.36"
    stat[28] = "2.5"
    stat[30] = "3.5"

    stat2 = [""] * 21
    stat2[0:6] = ["0", "000001", "20260728", "118551.49", "", "106279.64"]
    stat2[13] = "881386"
    stat2[16:19] = ["40.000", "12.040", "9.990"]

    return {
        "tdxzs.cfg": "旧概念|880099|4|2|0|旧概念\r\n".encode("gbk"),
        "tdxzs3.cfg": (
            "概念全称|880001|4|2|0|概念全称\r\n"
            "北京板块|880207|3|1|0|7\r\n"
            "银行|880301|2|1|0|T01\r\n"
            "银行|881001|12|1|0|X03\r\n"
            "测试风格|880701|5|1|0|测试风格\r\n"
        ).encode("gbk"),
        "tdxbk.cfg": "1|概念简称|概念全称|0\r\n".encode("gbk"),
        "spblock.dat": "#中证2000\r\n1600036\r\n0000001\r\n".encode("gbk"),
        "xgsg.cfg": "0|301717|20260731|23.45|||||||||||测试新股|||\r\n".encode("gbk"),
        "tdxstat.cfg": ("|".join(stat) + "\r\n").encode("gbk"),
        "tdxstat2.cfg": ("|".join(stat2) + "\r\n").encode("gbk"),
    }


def test_unpack_zhb_returns_immutable_bytes_mapping() -> None:
    files = unpack_zhb(_zip({"tdxzs.cfg": b"data"}))

    assert files["tdxzs.cfg"] == b"data"
    with pytest.raises(TypeError):
        files["other.cfg"] = b"x"  # type: ignore[index]


def test_unpack_zhb_rejects_unsafe_member_paths() -> None:
    with pytest.raises(ConfigArchiveError, match="unsafe"):
        unpack_zhb(_zip({"../escape.cfg": b"data"}))


def test_zhb_registry_loads_once_and_refreshes_after_ttl() -> None:
    clock = Clock()
    calls: list[int] = []
    registry = ZhbRegistry(ttl_seconds=10, time_fn=clock)

    def load() -> bytes:
        calls.append(len(calls) + 1)
        return _zip({"version": str(calls[-1]).encode()})

    first = registry.get(load)
    assert registry.get(load) is first
    assert first.files["version"] == b"1"

    clock.value += 10
    second = registry.get(load)
    assert second is not first
    assert second.files["version"] == b"2"


def test_zhb_registry_serializes_concurrent_initial_load() -> None:
    started = threading.Event()
    release = threading.Event()
    calls = 0
    registry = ZhbRegistry()

    def load() -> bytes:
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=5)
        return _zip({"file": b"data"})

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(registry.get, load) for _ in range(4)]
        assert started.wait(timeout=5)
        release.set()
        snapshots = [future.result(timeout=5) for future in futures]

    assert calls == 1
    assert all(snapshot is snapshots[0] for snapshot in snapshots)


def test_config_parsers_expose_verified_fields_and_preserve_raw_fields() -> None:
    files = _config_files()

    assert parse_tdx_block_indexes(files["tdxzs3.cfg"])[0] == {
        "name": "概念全称",
        "code": "880001",
        "type": 4,
        "subtype": 2,
        "reference": "概念全称",
        "category": "concept",
        "category_name": "概念板块",
        "taxonomy": None,
    }
    assert parse_tdx_block_indexes(files["tdxzs3.cfg"])[3] == {
        "name": "银行",
        "code": "881001",
        "type": 12,
        "subtype": 1,
        "reference": "X03",
        "category": "industry",
        "category_name": "行业板块",
        "taxonomy": "sw",
    }
    assert parse_tdx_block_aliases(files["tdxbk.cfg"]) == [
        {"short_name": "概念简称", "full_name": "概念全称"}
    ]
    assert parse_sp_blocks(files["spblock.dat"]) == [
        {"blockname": "中证2000", "count": 2, "codes": ("1600036", "0000001")}
    ]

    ipo = parse_ipo_subscriptions(files["xgsg.cfg"])[0]
    assert ipo["code"] == "301717"
    assert ipo["issue_price"] == 23.45
    assert isinstance(ipo["raw_fields"], tuple)

    stat = parse_stock_statistics(files["tdxstat.cfg"])[0]
    assert stat["pe_ttm"] == 5.05
    assert stat["change_5d"] == 2.5
    assert stat["change_10d"] == 3.5

    stat2 = parse_stock_statistics2(files["tdxstat2.cfg"])[0]
    assert stat2["amount"] == 118551.49
    assert stat2["block_index"] == "881386"
    assert stat2["high_52w"] == 12.04


def test_industry_parser_returns_tdx_and_sw_codes() -> None:
    data = "0|000001|T01|||X03\r\n1|600036|T02|||X04\r\n".encode("gbk")

    assert parse_tdx_industries(data) == [
        {"market": 0, "code": "000001", "tdx_industry": "T01", "sw_industry": "X03"},
        {"market": 1, "code": "600036", "tdx_industry": "T02", "sw_industry": "X04"},
    ]


def test_infoharbor_block_parser_preserves_full_membership_and_empty_groups() -> None:
    data = (
        "#GN_5G概念,3,880506,20050607,20260609,,\r\n"
        "0#000001,1#600000,\r\n"
        "2#430090\r\n"
        "#FG_融资融券,0,,,,,\r\n"
        "#ZS_沪深300,2,,20050408,,,\r\n"
        "0#000001,1#600000\r\n"
    ).encode("gbk")

    blocks = parse_infoharbor_blocks(data)
    assert blocks[0]["category_name"] == "概念板块"
    assert blocks[0]["declared_count"] == 3
    assert blocks[0]["actual_count"] == 3
    assert blocks[0]["members"][-1] == {"market": 2, "code": "430090"}
    assert blocks[1]["actual_count"] == 0
    assert blocks[2]["category"] == "index"


def test_base_finance_parser_exposes_bulk_region_assignments() -> None:
    data = _base_dbf(
        [
            (0, "000008", "20260808", 7, 8),
            (1, "600000", "20260808", 16, 1),
        ]
    )

    assert parse_tdx_base_finance(data) == [
        {
            "market": 0,
            "code": "000008",
            "province": 7,
            "industry": 8,
            "updated_date": "20260808",
        },
        {
            "market": 1,
            "code": "600000",
            "province": 16,
            "industry": 1,
            "updated_date": "20260808",
        },
    ]


def test_tdx_block_indexes_falls_back_to_legacy_catalog() -> None:
    client = SyncClient(config_registry=ZhbRegistry())
    payload = _zip(
        {
            "tdxzs.cfg": "旧概念|880099|4|2|0|旧概念\r\n".encode("gbk"),
        }
    )
    client.report_file = lambda filename, max_bytes=0: payload  # type: ignore[method-assign]

    assert client.tdx_block_indexes()[0]["code"] == "880099"
    assert client.tdx_block_indexes()[0]["category_name"] == "概念板块"


def test_sync_client_config_facade_reuses_snapshot_and_maps_block_indexes() -> None:
    registry = ZhbRegistry()
    client = SyncClient(config_registry=registry)
    payload = _zip(_config_files())
    report_calls: list[str] = []
    client.report_file = lambda filename, max_bytes=0: report_calls.append(filename) or payload  # type: ignore[method-assign]
    def block(block_file="block.dat"):
        names = {
            "block_gn.dat": "概念简称",
            "block_fg.dat": "测试风格",
            "block_zs.dat": "沪深300",
        }
        return [
            {
                "blockname": names.get(block_file, "概念简称"),
                "block_type": 1,
                "code_index": 0,
                "code": "600036",
            }
        ]

    client.block = block  # type: ignore[method-assign]

    def block_file_raw(filename):
        if filename == "infoharbor_block.dat":
            return (
                "#GN_概念简称,2,880001,20050607,20260808,,\r\n"
                "1#600036,0#000001\r\n"
                "#ZS_沪深300,1,,20050408,,,\r\n"
                "1#600036\r\n"
                "#ZS_空指数,0,,,,,\r\n"
            ).encode("gbk")
        if filename == "base.dbf":
            return _base_dbf(
                [
                    (0, "000008", "20260808", 7, 8),
                    (1, "600000", "20260808", 16, 1),
                ]
            )
        return b"0|000001|T01|||X03\r\n"

    client.block_file_raw = block_file_raw  # type: ignore[method-assign]

    indexes = client.tdx_block_indexes()
    assert {row["code"] for row in indexes} == {"880001", "880207", "880301", "881001", "880701"}
    assert next(row for row in indexes if row["code"] == "881001")["taxonomy"] == "sw"
    assert client.block_with_index()[0]["block_index"] == "880001"
    catalog = client.block_catalog()
    assert {row["category_name"] for row in catalog} == {
        "地区板块",
        "行业板块",
        "概念板块",
        "风格板块",
        "指数板块",
    }
    assert {row["taxonomy"] for row in client.block_catalog("行业")} == {"tdx", "sw"}
    concept_members = client.block_members("880001")
    assert [row["code"] for row in concept_members] == ["600036", "000001"]
    assert concept_members[0]["source"] == "infoharbor_block.dat"
    assert client.block_members("880301")[0]["code"] == "000001"
    assert client.block_members("881001")[0]["code"] == "000001"
    with pytest.raises(ValueError, match="ambiguous block"):
        client.block_members("银行", category="行业")
    assert client.block_members("沪深300", category="指数")[0]["source"] == "infoharbor_block.dat"
    assert any(row["name"] == "空指数" for row in client.block_catalog("指数"))
    region_members = client.block_members("880207")
    assert [row["code"] for row in region_members] == ["000008"]
    assert region_members[0]["source"] == "base.dbf"
    assert client.sp_blocks("中证2000")[0]["count"] == 2
    assert client.ipo_subscriptions()[0]["name"] == "测试新股"
    assert client.stock_statistics()[0]["date"] == "20260728"
    assert client.stock_statistics2()[0]["block_index"] == "881386"
    assert client.tdx_industries()[0]["sw_industry"] == "X03"
    assert report_calls == ["zhb.zip"]
