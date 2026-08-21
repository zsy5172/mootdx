from __future__ import annotations

import datetime as dt

import httpx
import pytest

from mootdx_next.api.clients import AsyncClient
from mootdx_next.api.clients import SyncClient
from mootdx_next.errors import EtfPcfDecodeError
from mootdx_next.etf import ETF_PCF_URL
from mootdx_next.etf import EtfPcfHttpProvider
from mootdx_next.etf import decode_etf_pcf


PAYLOAD = (
    '[{"shfe":"4465625.92","cfgs":"300","sgfe":"900000",'
    '"jjjc":"300ETF","ygxj":"93776.92","xjce":"93218.92",'
    '"jzrq":"20260629","xjtdbl":"50.00","sgshqk":"申购和赎回皆允许",'
    '"jzrfe":"1891488.77"}]'
)


def test_decode_etf_pcf_accepts_summary_and_empty_upstream_response() -> None:
    rows = decode_etf_pcf(PAYLOAD)
    assert rows[0]["cfgs"] == "300"
    assert decode_etf_pcf('{"ErrorCode":-1006,"ErrorInfo":"空数据"}') == []

    with pytest.raises(EtfPcfDecodeError, match="not valid JSON"):
        decode_etf_pcf("not-json")


def test_etf_pcf_provider_uses_tdx_quantload_query() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL(
            f"{ETF_PCF_URL}?type=etf&code=510300&rq=20260630"
        )
        assert request.headers["user-agent"] == "mootdx"
        return httpx.Response(200, text=PAYLOAD)

    provider = EtfPcfHttpProvider(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    rows = provider.load("510300.SH", dt.date(2026, 6, 30))
    assert rows[0]["code"] == "510300"
    assert rows[0]["date"] == "20260630"
    assert rows[0]["source"] == "tdx_quantload_etf"


def test_sync_and_async_clients_expose_etf_pcf_provider() -> None:
    class Provider:
        def load(self, code, trading_date):
            return [{"code": code, "date": str(trading_date)}]

    provider = Provider()
    client = SyncClient(etf_pcf_provider=provider)
    assert client.etf_pcf("510300.SH", "20260630")[0]["code"] == "510300.SH"
    assert client.request("etf_pcf", code="510300.SH", trading_date="20260630")

    async def run() -> None:
        async_client = AsyncClient(etf_pcf_provider=provider)
        assert (await async_client.etf_pcf("510300.SH", "20260630"))[0]["date"] == "20260630"

    import asyncio

    asyncio.run(run())
