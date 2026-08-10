from __future__ import annotations

import asyncio
import struct
import threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from zipfile import ZipFile

import httpx
import pytest

from mootdx_next.api.clients import AsyncClient
from mootdx_next.api.clients import SyncClient
from mootdx_next.api.pandas import PandasClient
from mootdx_next.errors import GbbqArchiveError
from mootdx_next.errors import GbbqDecodeError
from mootdx_next.errors import GbbqDownloadError
from mootdx_next.gbbq import decode_gbbq
from mootdx_next.gbbq import extract_gbbq_member
from mootdx_next.gbbq import GbbqHttpProvider
from mootdx_next.gbbq import GbbqRegistry

SZ_FIRST_RECORD = bytes.fromhex(
    "631224b0f311c9a953c0bb1806866a39dcbc5c5514c3e1d2000000803f"
)
SPECIAL_MARKET_RECORD = bytes.fromhex(
    "3758ac942be322b483d6fe5bb62f580a50940abb96412e270000000000"
)


def _content(*records: bytes) -> bytes:
    return struct.pack("<I", len(records)) + b"".join(records)


def _archive(content: bytes, *, name: str = "gbbq") -> bytes:
    target = BytesIO()
    with ZipFile(target, "w") as archive:
        archive.writestr(name, content)
    return target.getvalue()


class StaticProvider:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls = 0

    def load(self) -> bytes:
        self.calls += 1
        return self.payload


def test_decode_gbbq_preserves_market_and_decodes_verified_records() -> None:
    events = decode_gbbq(_content(SZ_FIRST_RECORD, SPECIAL_MARKET_RECORD))

    first = events[0]
    assert (first.market, first.code, first.date, first.category) == (
        0,
        "000001",
        "1990-03-01",
        1,
    )
    assert first.c2 == pytest.approx(3.56)
    assert first.to_dict()["peigujia"] == pytest.approx(3.56)

    special = events[1]
    assert (special.market, special.code, special.date, special.category) == (
        99,
        "519666",
        "2013-04-18",
        1,
    )
    assert special.symbol == "m99519666"


def test_decode_gbbq_requires_exact_record_boundaries() -> None:
    with pytest.raises(GbbqDecodeError, match="expected"):
        decode_gbbq(struct.pack("<I", 1) + SZ_FIRST_RECORD[:-1])
    with pytest.raises(GbbqDecodeError, match="expected"):
        decode_gbbq(_content(SZ_FIRST_RECORD) + b"\x00")


def test_extract_gbbq_member_validates_archive_and_member_paths() -> None:
    content = _content(SZ_FIRST_RECORD)
    assert extract_gbbq_member(_archive(content)) == content

    with pytest.raises(GbbqArchiveError, match="unsafe"):
        extract_gbbq_member(_archive(content, name="../gbbq"))
    with pytest.raises(GbbqArchiveError, match="does not contain"):
        extract_gbbq_member(_archive(content, name="other"))


def test_gbbq_http_provider_streams_with_a_size_limit() -> None:
    payload = _archive(_content(SZ_FIRST_RECORD))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.scheme == "https"
        return httpx.Response(200, headers={"Content-Length": str(len(payload))}, content=payload)

    provider = GbbqHttpProvider(
        max_bytes=len(payload),
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert provider.load() == payload

    rejected = GbbqHttpProvider(
        max_bytes=len(payload) - 1,
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(GbbqDownloadError, match="exceeds"):
        rejected.load()


def test_gbbq_registry_serializes_load_and_exposes_immutable_index() -> None:
    payload = _archive(_content(SZ_FIRST_RECORD))
    started = threading.Event()
    release = threading.Event()
    calls = 0
    registry = GbbqRegistry()

    def load() -> bytes:
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=5)
        return payload

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(registry.get, load) for _ in range(4)]
        assert started.wait(timeout=5)
        release.set()
        snapshots = [future.result(timeout=5) for future in futures]

    assert calls == 1
    assert all(snapshot is snapshots[0] for snapshot in snapshots)
    assert snapshots[0].find(0, "000001")[0].code == "000001"
    with pytest.raises(TypeError):
        snapshots[0].by_security[(1, "600036")] = ()  # type: ignore[index]


def test_gbbq_client_facades_share_snapshot_and_support_tdx_fallback() -> None:
    provider = StaticProvider(_archive(_content(SZ_FIRST_RECORD)))
    sync_client = SyncClient(gbbq_registry=GbbqRegistry(), gbbq_provider=provider)

    first = sync_client.gbbq("sz000001", fallback=False)
    assert first[0]["source"] == "gbbq.zip"
    assert sync_client.gbbq_all()[0]["code"] == "000001"
    assert provider.calls == 1

    async_rows = asyncio.run(AsyncClient(sync_client=sync_client).gbbq("000001"))
    assert async_rows == first
    frame = PandasClient(raw_client=sync_client).gbbq("000001")
    assert frame.iloc[0]["peigujia"] == pytest.approx(3.56)

    failed = SyncClient(
        gbbq_registry=GbbqRegistry(),
        gbbq_provider=StaticProvider(b"not a zip"),
    )
    failed.xdxr = lambda symbol: [{"market": 1, "code": "600036"}]  # type: ignore[method-assign]
    assert failed.gbbq("600036", fallback=True)[0]["source"] == "tdx"
    with pytest.raises(GbbqArchiveError):
        failed.gbbq("600036")
