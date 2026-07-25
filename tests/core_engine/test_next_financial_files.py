from __future__ import annotations

import asyncio
import hashlib
import io
import struct
import zipfile
from pathlib import Path

import httpx
import pandas as pd
import pandas.testing as pdt
import pytest

from mootdx_next import AsyncFinancialFileClient
from mootdx_next import FinancialCatalogError
from mootdx_next import FinancialFileClient
from mootdx_next import FinancialIntegrityError
from mootdx_next import FinancialReader
from mootdx_next import ServerEndpoint
from mootdx_next import UnsafeArchiveError
from mootdx_next.financial.client import parse_catalog
from mootdx.financial.financial import Financial
from mootdx.financial.financial import FinancialList
from mootdx.financial.financial import FinancialReader as LegacyFinancialReader


def _dat_payload() -> bytes:
    header = struct.pack("<1hI1H3L", 0, 20231231, 1, 0, 8, 0)
    data_offset = len(header) + struct.calcsize("<6s1c1L")
    stock = struct.pack("<6s1c1L", b"600036", b"\x00", data_offset)
    report = struct.pack("<2f", 1.25, 2.5)
    return header + stock + report


def _zip_payload(dat: bytes | None = None, *, member: str = "gpcw20231231.dat") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, dat or _dat_payload())
    return output.getvalue()


def _catalog(file_content: bytes, filename: str = "gpcw20231231.zip") -> bytes:
    digest = hashlib.md5(file_content).hexdigest()
    return f"{filename},{digest},{len(file_content)}\n".encode()


def test_financial_reader_parses_dat_zip_and_suffixless_paths(tmp_path: Path) -> None:
    dat_path = tmp_path / "gpcw20231231.dat"
    zip_path = tmp_path / "gpcw20231231.zip"
    dat_path.write_bytes(_dat_payload())
    zip_path.write_bytes(_zip_payload())

    dat_frame = FinancialReader.read(dat_path)
    zip_frame = FinancialReader.read(zip_path)
    suffixless_frame = FinancialReader.read(tmp_path / "gpcw20231231")

    pdt.assert_frame_equal(dat_frame, zip_frame)
    pdt.assert_frame_equal(zip_frame, suffixless_frame)
    assert list(dat_frame.index) == ["600036"]
    assert dat_frame.loc["600036", "report_date"] == 20231231
    assert dat_frame.loc["600036", "基本每股收益"] == pytest.approx(1.25)


def test_legacy_financial_reader_is_the_next_reader() -> None:
    assert LegacyFinancialReader is FinancialReader


@pytest.mark.parametrize(("suffix", "payload"), [(".dat", _dat_payload()), (".zip", _zip_payload())])
def test_legacy_raw_financial_facade_uses_next_in_memory_parser(
    tmp_path: Path,
    suffix: str,
    payload: bytes,
) -> None:
    path = tmp_path / f"gpcw20231231{suffix}"
    path.write_bytes(payload)

    with path.open("rb") as stream:
        rows = Financial().parse(stream)

    pdt.assert_frame_equal(Financial.to_df(rows), FinancialReader.from_bytes(payload, file_type=suffix))


def test_legacy_catalog_facade_uses_next_catalog_validation(tmp_path: Path) -> None:
    payload = _zip_payload()
    path = tmp_path / "gpcw.txt"
    path.write_bytes(_catalog(payload))

    with path.open("rb") as stream:
        files = FinancialList().parse(stream)

    assert files == [
        {
            "filename": "gpcw20231231.zip",
            "hash": hashlib.md5(payload).hexdigest(),
            "filesize": len(payload),
        }
    ]


def test_financial_reader_rejects_unsafe_zip_member(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.zip"
    path.write_bytes(_zip_payload(member="../escape.dat"))

    with pytest.raises(UnsafeArchiveError, match="unsafe"):
        FinancialReader.read(path)


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"gpcw20231231.zip,not-md5,10\n",
        b"../gpcw20231231.zip,00000000000000000000000000000000,10\n",
    ],
)
def test_financial_catalog_rejects_malformed_content(content: bytes) -> None:
    with pytest.raises(FinancialCatalogError):
        parse_catalog(content)


def test_sync_financial_client_downloads_verifies_and_reuses_file(tmp_path: Path) -> None:
    payload = _zip_payload()
    catalog = _catalog(payload)
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("/gpcw.txt"):
            return httpx.Response(200, content=catalog)
        return httpx.Response(200, content=payload)

    progress = []
    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = FinancialFileClient(http_client=http)
    try:
        path = client.fetch(
            downdir=tmp_path,
            filename="gpcw20231231.zip",
            report_hook=lambda downloaded, total: progress.append((downloaded, total)),
        )
        same = client.fetch(downdir=tmp_path, filename="gpcw20231231.zip")
        frame = client.parse(downdir=tmp_path, filename="gpcw20231231.zip")
    finally:
        client.close()
        http.close()

    assert path == same == tmp_path / "gpcw20231231.zip"
    assert path.read_bytes() == payload
    assert progress[-1] == (len(payload), len(payload))
    assert requests.count("/tdxfin/gpcw20231231.zip") == 1
    assert isinstance(frame, pd.DataFrame)


def test_integrity_failure_does_not_use_protocol_fallback(tmp_path: Path) -> None:
    expected = _zip_payload()
    catalog = _catalog(expected)
    fallback_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/gpcw.txt"):
            return httpx.Response(200, content=catalog)
        return httpx.Response(200, content=b"wrong")

    def fallback_factory(endpoint):
        fallback_calls.append(endpoint)
        raise AssertionError("integrity failures must not use fallback")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = FinancialFileClient(
        http_client=http,
        report_servers=[ServerEndpoint("127.0.0.1", 7709)],
        report_client_factory=fallback_factory,
    )
    try:
        with pytest.raises(FinancialIntegrityError):
            client.fetch(downdir=tmp_path, filename="gpcw20231231.zip")
    finally:
        http.close()

    assert fallback_calls == []
    assert list(tmp_path.glob("*.tmp")) == []


def test_report_protocol_is_used_only_after_retryable_https_failure(tmp_path: Path) -> None:
    payload = _zip_payload()
    catalog = _catalog(payload)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    class ReportClient:
        def __init__(self, endpoint) -> None:
            self.endpoint = endpoint

        def fetch_file(self, filename, filesize=0, reporthook=None):
            calls.append((filename, filesize))
            return bytearray(catalog if filename.endswith("gpcw.txt") else payload)

        def close(self) -> None:
            calls.append(("close", self.endpoint.host))

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = FinancialFileClient(
        http_client=http,
        report_servers=[ServerEndpoint("127.0.0.1", 7709)],
        report_client_factory=ReportClient,
    )
    try:
        path = client.fetch(downdir=tmp_path, filename="gpcw20231231.zip")
    finally:
        http.close()

    assert path.read_bytes() == payload
    assert ("tdxfin/gpcw.txt", 0) in calls
    assert ("tdxfin/gpcw20231231.zip", len(payload)) in calls


def test_async_financial_client_matches_sync_download_and_parse(tmp_path: Path) -> None:
    payload = _zip_payload()
    catalog = _catalog(payload)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/gpcw.txt"):
            return httpx.Response(200, content=catalog)
        return httpx.Response(200, content=payload)

    async def run() -> None:
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = AsyncFinancialFileClient(http_client=http)
        try:
            path = await client.fetch(downdir=tmp_path, filename="gpcw20231231.zip")
            frame = await client.parse(downdir=tmp_path, filename="gpcw20231231.zip")
        finally:
            await http.aclose()

        assert path.read_bytes() == payload
        expected = FinancialReader.read(path)
        pdt.assert_frame_equal(frame, expected)

    asyncio.run(run())
