from __future__ import annotations

import pandas as pd
import pytest

from mootdx_next.adapters import bars_to_frame
from mootdx_next.adapters import block_to_frame
from mootdx_next.adapters import f10_categories_to_frame
from mootdx_next.adapters import finance_to_frame
from mootdx_next.adapters import limit_prices_to_frame
from mootdx_next.adapters import minutes_to_frame
from mootdx_next.adapters import price_limit_to_frame
from mootdx_next.adapters import quotes_to_frame
from mootdx_next.adapters import stocks_to_frame
from mootdx_next.adapters import transaction_to_frame
from mootdx_next.adapters import transactions_to_frame
from mootdx_next.adapters import xdxr_to_frame


@pytest.mark.parametrize(
    ("converter", "rows", "indexed"),
    [
        (stocks_to_frame, [{"code": "600036", "name": "招商银行"}], False),
        (quotes_to_frame, [{"code": "600036", "price": 10.0, "vol": 1}], False),
        (
            limit_prices_to_frame,
            [{"market": 1, "code": "600053", "limit_up": 7.5, "limit_down": 6.14}],
            False,
        ),
        (bars_to_frame, [{"datetime": "2026-07-20 15:00", "close": 10.0}], True),
        (minutes_to_frame, [{"date": "2026-07-20 09:30", "price": 10.0, "vol": 1}], True),
        (transaction_to_frame, [{"time": "09:30", "price": 10.0, "vol": 1}], False),
        (transactions_to_frame, [{"time": "09:30", "price": 10.0, "vol": 1}], False),
        (xdxr_to_frame, [{"year": 2026, "category": 1}], False),
        (f10_categories_to_frame, [{"name": "最新提示", "length": 10}], False),
        (block_to_frame, [{"blockname": "测试板块", "code": "600036"}], False),
    ],
)
def test_record_adapter_matrix(converter, rows: list[dict[str, object]], indexed: bool) -> None:
    result = converter(rows)

    assert isinstance(result, pd.DataFrame)
    assert result.to_dict(orient="records")
    if indexed:
        assert isinstance(result.index, pd.DatetimeIndex)


@pytest.mark.parametrize(
    "converter",
    [
        stocks_to_frame,
        quotes_to_frame,
        limit_prices_to_frame,
        bars_to_frame,
        minutes_to_frame,
        transaction_to_frame,
        transactions_to_frame,
        xdxr_to_frame,
        f10_categories_to_frame,
        block_to_frame,
    ],
)
def test_record_adapters_share_empty_frame_contract(converter) -> None:
    result = converter([])

    assert isinstance(result, pd.DataFrame)
    assert result.empty


@pytest.mark.parametrize("row", [None, {}, {"code": "600036", "liutongguben": 1.0}])
def test_finance_adapter_matrix(row: dict[str, object] | None) -> None:
    result = finance_to_frame(row)

    assert isinstance(result, pd.DataFrame)
    assert result.empty is (not row)


@pytest.mark.parametrize(
    "row",
    [
        None,
        {},
        {
            "market": 1,
            "code": "600036",
            "limit_up": 42.9,
            "limit_down": 35.1,
            "source": "calculated",
        },
    ],
)
def test_price_limit_adapter_matrix(row: dict[str, object] | None) -> None:
    result = price_limit_to_frame(row)

    assert isinstance(result, pd.DataFrame)
    assert list(result.columns) == ["market", "code", "limit_up", "limit_down", "source"]
    assert result.empty is (not row)
