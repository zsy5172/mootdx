from __future__ import annotations

import pandas as pd
import pytest

from mootdx_next import aggregate_block_quotes


def _member(
    block_code: str, block_name: str, market: int, code: str
) -> dict[str, object]:
    return {
        "block_code": block_code,
        "block_name": block_name,
        "block_category": "concept",
        "block_category_name": "概念板块",
        "block_taxonomy": None,
        "market": market,
        "code": code,
    }


def test_aggregate_block_quotes_uses_only_caller_provided_records() -> None:
    quotes = pd.DataFrame.from_records(
        [
            {
                "market": 1,
                "code": "600001",
                "price": 11.0,
                "last_close": 10.0,
                "amount": 100.0,
                "volume": 10.0,
                "limit_up": 11.0,
                "limit_down": 9.0,
            },
            {
                "market": 0,
                "code": "300001",
                "price": 9.0,
                "last_close": 10.0,
                "amount": 300.0,
                "volume": 30.0,
                "limit_up": 12.0,
                "limit_down": 8.0,
            },
            {
                "market": 2,
                "code": "920001",
                "price": 13.0,
                "last_close": 10.0,
                "amount": 50.0,
                "volume": 5.0,
            },
        ]
    )
    members = [
        _member("880001", "测试概念", 1, "600001"),
        _member("880001", "测试概念", 0, "300001"),
        _member("880001", "测试概念", 2, "920999"),
        _member("880002", "北证概念", 2, "920001"),
    ]
    blocks = [
        {
            "code": "880001",
            "name": "测试概念",
            "category": "concept",
            "category_name": "概念板块",
            "taxonomy": None,
        },
        {
            "code": "880002",
            "name": "北证概念",
            "category": "concept",
            "category_name": "概念板块",
            "taxonomy": None,
        },
        {
            "code": "880003",
            "name": "空概念",
            "category": "concept",
            "category_name": "概念板块",
            "taxonomy": None,
        },
    ]

    result = aggregate_block_quotes(quotes, members, blocks).set_index("block_code")

    concept = result.loc["880001"]
    assert concept["member_count"] == 3
    assert concept["quoted_count"] == 2
    assert concept["coverage_ratio"] == pytest.approx(2 / 3)
    assert concept["amount"] == 400.0
    assert concept["volume"] == 40.0
    assert concept["weighted_change_pct"] == pytest.approx(-5.0)
    assert concept["median_change_pct"] == pytest.approx(0.0)
    assert concept["up_count"] == 1
    assert concept["down_count"] == 1
    assert concept["limit_up_count"] == 1

    assert result.loc["880002", "weighted_change_pct"] == pytest.approx(30.0)
    assert result.loc["880002", "quoted_count"] == 1
    assert result.loc["880003", "member_count"] == 0
    assert pd.isna(result.loc["880003", "coverage_ratio"])


def test_aggregate_block_quotes_rejects_ambiguous_inputs() -> None:
    quote = {"market": 1, "code": "600001", "price": 10.0, "last_close": 9.0}

    with pytest.raises(ValueError, match="duplicate quote"):
        aggregate_block_quotes([quote, quote], [])

    with pytest.raises(ValueError, match="invalid market"):
        aggregate_block_quotes(
            [], [{"block_name": "测试", "block_category": "concept", "code": "600001"}]
        )


def test_aggregate_block_quotes_returns_a_typed_empty_frame() -> None:
    result = aggregate_block_quotes([], [])

    assert result.empty
    assert "weighted_change_pct" in result.columns
