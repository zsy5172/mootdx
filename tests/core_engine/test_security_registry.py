from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from mootdx_next.api.clients import SyncClient
from mootdx_next.errors import InvalidSymbolError
from mootdx_next.securities import Security
from mootdx_next.securities import SecurityRegistry
from mootdx_next.securities import security_official_metadata


class Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def _security(code: str = "600036") -> Security:
    return Security(
        market=1,
        code=code,
        name="招商银行",
        security_type="stock",
        volunit=100,
        decimal_point=2,
        pre_close=38.0,
    )


def test_registry_reuses_refreshes_and_invalidates_immutable_snapshot() -> None:
    clock = Clock()
    calls = 0

    def loader():
        nonlocal calls
        calls += 1
        return [_security(f"{600035 + calls:06d}")]

    registry = SecurityRegistry(ttl_seconds=10, time_fn=clock)
    first = registry.get(loader)
    assert isinstance(first, tuple)
    assert registry.find(1, first[0].code) is first[0]
    assert registry.find(0, first[0].code) is None
    assert registry.get(loader) is first
    assert calls == 1

    clock.value += 10
    assert registry.find(1, first[0].code) is None
    second = registry.get(loader)
    assert second is not first
    assert registry.find(1, first[0].code) is None
    assert registry.find(1, second[0].code) is second[0]
    registry.invalidate()
    assert registry.get(loader)[0].code == "600038"
    assert registry.refresh(loader)[0].code == "600039"


def test_registry_performs_only_one_concurrent_load() -> None:
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def loader():
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=5)
        return [_security()]

    registry = SecurityRegistry()
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(registry.get, loader) for _ in range(4)]
        assert started.wait(timeout=5)
        release.set()
        snapshots = [future.result(timeout=5) for future in futures]

    assert calls == 1
    assert all(snapshot is snapshots[0] for snapshot in snapshots)


@pytest.mark.parametrize(
    "values",
    [
        [object()],
        [Security(9, "600036", "错误市场", "stock")],
        [Security(1, "ABC", "错误代码", "stock")],
        [Security(1, "600036", "错误类型", "bond")],
        [_security(), _security()],
    ],
)
def test_registry_rejects_invalid_snapshots(values: list[object]) -> None:
    registry = SecurityRegistry()

    with pytest.raises((TypeError, ValueError)):
        registry.get(lambda: values)  # type: ignore[arg-type]


class DirectoryClient(SyncClient):
    def __init__(self) -> None:
        super().__init__(security_registry=SecurityRegistry())
        self.market_calls: list[int] = []

    def stocks(self, market: int, refresh: bool = False) -> list[dict[str, object]]:
        self.market_calls.append(market)
        rows = {
            1: [
                {"code": "600036", "name": "招商银行", "volunit": 100, "decimal_point": 2},
                {"code": "510300", "name": "沪深300ETF", "volunit": 100, "decimal_point": 3},
                {"code": "000001", "name": "上证指数", "volunit": 100, "decimal_point": 2},
            ],
            0: [
                {"code": "000001", "name": "平安银行", "volunit": 100, "decimal_point": 2},
                {"code": "159919", "name": "沪深300ETF", "volunit": 100, "decimal_point": 3},
                {"code": "399001", "name": "深证成指", "volunit": 100, "decimal_point": 2},
            ],
            2: [
                {
                    "code": "920786",
                    "name": "骑士乳业",
                    "volunit": 100,
                    "decimal_point": 2,
                    "source": "bse",
                }
            ],
        }
        return rows[market]


def test_client_exposes_typed_directory_and_code_filters() -> None:
    client = DirectoryClient()

    securities = client.securities()

    assert client.market_calls == [1, 0, 2]
    assert securities[0]["symbol"] == "sh600036"
    assert client.stock_codes() == ["sh600036", "sz000001", "bj920786"]
    assert client.etf_codes() == ["sh510300", "sz159919"]
    assert client.index_codes() == ["sh000001", "sz399001"]
    assert client.security("SH.600036")["name"] == "招商银行"  # type: ignore[index]
    assert client.security("sh600000") is None
    assert client.market_calls == [1, 0, 2]

    by_symbol = {row["symbol"]: row for row in securities}
    assert {
        key: by_symbol["sh600036"][key]
        for key in (
            "exchange",
            "exchange_name",
            "board",
            "board_name",
            "security_type_name",
        )
    } == {
        "exchange": "SSE",
        "exchange_name": "上海证券交易所",
        "board": "main",
        "board_name": "主板",
        "security_type_name": "A股",
    }
    assert by_symbol["sz000001"]["exchange_name"] == "深圳证券交易所"
    assert by_symbol["bj920786"]["exchange_name"] == "北京证券交易所"
    assert by_symbol["bj920786"]["board"] is None
    assert by_symbol["sh600036"]["source_kind"] == "tdx_security_directory"
    assert by_symbol["bj920786"]["source_kind"] == "bse_market_snapshot"
    assert "bj899050" not in by_symbol


@pytest.mark.parametrize(
    ("market", "code", "board", "board_name"),
    [
        (1, "600036", "main", "主板"),
        (1, "688981", "star", "科创板"),
        (0, "000001", "main", "主板"),
        (0, "300750", "chinext", "创业板"),
        (2, "920786", None, None),
    ],
)
def test_security_metadata_uses_official_exchange_and_board_names(
    market: int,
    code: str,
    board: str | None,
    board_name: str | None,
) -> None:
    metadata = security_official_metadata(market, code, security_type="stock")

    assert metadata["board"] == board
    assert metadata["board_name"] == board_name
    assert metadata["security_type_name"] == "A股"


def test_client_security_refresh_reloads_all_markets() -> None:
    client = DirectoryClient()

    client.stock_codes()
    client.security("600036", refresh=True)

    assert client.market_calls == [1, 0, 2, 1, 0, 2]


def test_client_security_rejects_invalid_symbol() -> None:
    client = DirectoryClient()

    with pytest.raises(InvalidSymbolError):
        client.security("")
    with pytest.raises(InvalidSymbolError):
        client.security("ABC")
