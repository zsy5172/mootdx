from __future__ import annotations

from pathlib import Path

import pytest

from mootdx_next.api.clients import SyncClient
from mootdx_next.protocol import StdQuoteProtocol
from mootdx_next.securities import Security
from mootdx_next.securities import SecurityRegistry
from tests.core_engine.test_sync_client_stock_apis import RecordingConnectionPool
from tests.core_engine.test_sync_client_stock_apis import RecordingScheduler
from tests.core_engine.test_sync_client_stock_apis import RecordingTransport

ROOT = Path(__file__).resolve().parents[2]


def _body(api: str, case_id: str, step: str) -> bytes:
    return (ROOT / "compat" / "corpus" / api / case_id / "steps" / step / "response.body.bin").read_bytes()


def _registry(market: int, code: str, decimal_point: int) -> SecurityRegistry:
    registry = SecurityRegistry()
    registry.get(
        lambda: [
            Security(
                market=market,
                code=code,
                name="精度测试证券",
                security_type="other",
                decimal_point=decimal_point,
            )
        ]
    )
    return registry


def _client(body: bytes, registry: SecurityRegistry) -> SyncClient:
    transport = RecordingTransport(responses=[body])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    return SyncClient(
        protocol=StdQuoteProtocol(),
        connection_pool=pool,
        scheduler=scheduler,
        security_registry=registry,
    )


def test_protocol_decoders_accept_directory_price_precision_context() -> None:
    protocol = StdQuoteProtocol()
    quote_body = bytearray(_body("quotes", "single_sh", "01_quotes"))
    quote_body[4] = 0
    quote_body[5:11] = b"190039"

    quote = protocol.decode_quotes(
        bytes(quote_body),
        price_coefficients={(0, "190039"): 0.0001},
    )[0]
    minute = protocol.decode_minutes(
        _body("minutes", "history_sh_000001_20171010", "01_minutes"),
        0,
        "190039",
        price_coefficient=0.0001,
    )[0]
    trade = protocol.decode_transaction(
        _body("transaction", "live_sh_600036_last10", "01_transaction"),
        market=0,
        code="190039",
        price_coefficient=0.0001,
    )[0]

    assert quote["price"] == pytest.approx(0.3921)
    assert minute["price"] == pytest.approx(0.1135)
    assert trade["price"] == pytest.approx(0.3921)


def test_sync_client_uses_registry_precision_for_quotes_minutes_and_trades() -> None:
    registry = _registry(0, "190039", 4)
    quote_body = bytearray(_body("quotes", "single_sh", "01_quotes"))
    quote_body[4] = 0
    quote_body[5:11] = b"190039"

    quote = _client(bytes(quote_body), registry).quotes("sz190039")[0]
    minute = _client(
        _body("minutes", "history_sh_000001_20171010", "01_minutes"),
        registry,
    ).minutes("sz190039", "20171010")[0]
    trade = _client(
        _body("transaction", "live_sh_600036_last10", "01_transaction"),
        registry,
    ).transaction("sz190039", offset=10)[0]
    history = _client(
        _body("transactions", "history_sh_600036_20170209_last10", "01_transactions"),
        registry,
    ).transactions("sz190039", "20170209", offset=10)[0]

    assert quote["price"] == pytest.approx(0.3921)
    assert minute["price"] == pytest.approx(0.1135)
    assert trade["price"] == pytest.approx(0.3921)
    assert history["price"] == pytest.approx(0.1873)


def test_sync_client_loads_ambiguous_security_once_but_keeps_stock_fast_path() -> None:
    class DirectoryClient(SyncClient):
        def __init__(self) -> None:
            super().__init__(security_registry=SecurityRegistry())
            self.loads = 0

        def _load_security_directory(self) -> tuple[Security, ...]:
            self.loads += 1
            return (
                Security(0, "190039", "青岛2011", "other", decimal_point=4),
            )

    client = DirectoryClient()

    assert client._price_coefficient(1, "600036") == 0.01
    assert client.loads == 0
    assert client._price_coefficient(0, "190039") == 0.0001
    assert client._price_coefficient(0, "190039") == 0.0001
    assert client.loads == 1
