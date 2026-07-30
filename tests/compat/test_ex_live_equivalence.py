from __future__ import annotations

import os

import pytest

from mootdx_next.api.ex_clients import ExSyncClient
from mootdx_next.models import ServerEndpoint

pytestmark = pytest.mark.skipif(
    os.getenv("MOOTDX_NEXT_EX_LIVE") != "1",
    reason="set MOOTDX_NEXT_EX_LIVE=1 for live ExHq legacy comparison",
)

tdxpy_exhq = pytest.importorskip("tdxpy.exhq")

EX_HOST = os.getenv("MOOTDX_NEXT_EX_HOST", "116.205.143.214")
EX_PORT = int(os.getenv("MOOTDX_NEXT_EX_PORT", "7727"))
EX_DATE = int(os.getenv("MOOTDX_NEXT_EX_DATE", "20260729"))


def _assert_float_fields(left: dict, right: dict, fields: tuple[str, ...]) -> None:
    for field in fields:
        assert left[field] == pytest.approx(right[field], rel=1e-6, abs=1e-6)


def test_next_ex_matches_legacy_wire_results_or_documents_fixes() -> None:
    legacy = tdxpy_exhq.TdxExHq_API(raise_exception=True, auto_retry=False)
    assert legacy.connect(EX_HOST, EX_PORT, time_out=5)
    next_client = ExSyncClient(
        servers=[ServerEndpoint(EX_HOST, EX_PORT, "compat")],
        max_retries=0,
    )
    try:
        legacy_markets = legacy.get_markets()
        next_markets = next_client.markets()
        assert len(legacy_markets) == len(next_markets)
        for legacy_row, next_row in zip(legacy_markets, next_markets, strict=True):
            assert next_row == {
                "market": legacy_row["market"],
                "category": legacy_row["category"],
                "name": legacy_row["name"],
                "short_name": legacy_row["short_name"],
            }
        assert legacy.get_instrument_count() == next_client.instrument_count()

        legacy_instruments = legacy.get_instrument_info(0, 20)
        next_instruments = next_client.instrument(0, 20)
        assert len(legacy_instruments) == len(next_instruments) == 20
        for index, (legacy_row, next_row) in enumerate(
            zip(legacy_instruments, next_instruments, strict=True)
        ):
            assert next_row["start"] == index
            assert next_row["category"] == legacy_row["category"]
            assert next_row["market"] == legacy_row["market"]
            assert next_row["code"] == legacy_row["code"]
            assert next_row["name"] == legacy_row["name"]
            assert next_row["description"] == legacy_row["desc"]

        legacy_quote = dict(legacy.get_instrument_quote(31, "00700")[0])
        next_quote = next_client.quote(31, "00700")
        assert next_quote is not None
        assert next_quote["market"] == legacy_quote["market"]
        assert next_quote["code"] == legacy_quote["code"]
        assert next_quote["pre_close"] == pytest.approx(legacy_quote["pre_close"])
        for field in ("open", "high", "low", "price", "bid1", "ask1"):
            assert next_quote[field] == pytest.approx(legacy_quote[field], rel=0.02, abs=1.0)
        assert next_quote["open_interest"] == legacy_quote["kaicang"]
        assert next_quote["volume"] == legacy_quote["zongliang"]
        assert next_quote["position"] == legacy_quote["chicang"]

        legacy_bars = legacy.get_instrument_bars(9, 31, "00700", 0, 20)
        next_bars = next_client.bars(31, "00700", 9, 0, 20)
        assert len(legacy_bars) == len(next_bars) == 20
        # Exclude the newest bar because it may change between two sequential
        # live requests during a market session.
        for legacy_row, next_row in zip(legacy_bars[:-1], next_bars[:-1], strict=True):
            assert next_row["datetime"] == legacy_row["datetime"]
            _assert_float_fields(
                next_row,
                legacy_row,
                ("open", "high", "low", "close", "amount"),
            )
            assert next_row["position"] == legacy_row["position"]
            assert next_row["trade"] == legacy_row["trade"]
            assert next_row["settlement_price"] == pytest.approx(legacy_row["price"])

        legacy_minutes = legacy.get_history_minute_time_data(31, "00700", EX_DATE)
        next_minutes = next_client.minutes(31, "00700", EX_DATE)
        assert len(legacy_minutes) == len(next_minutes) > 0
        for legacy_row, next_row in zip(legacy_minutes[:20], next_minutes[:20], strict=True):
            assert next_row["hour"] == legacy_row["hour"]
            assert next_row["minute"] == legacy_row["minute"]
            assert next_row["price"] == pytest.approx(legacy_row["price"])
            assert next_row["average_price"] == pytest.approx(legacy_row["avg_price"])
            assert next_row["volume"] == legacy_row["volume"]

        legacy_trades = legacy.get_history_transaction_data(31, "00700", EX_DATE, 0, 20)
        next_trades = next_client.transactions(31, "00700", EX_DATE, 0, 20)
        assert len(legacy_trades) == len(next_trades) == 20
        for legacy_row, next_row in zip(legacy_trades, next_trades, strict=True):
            # Confirmed bug fix: legacy exposes the wire integer as price.
            assert next_row["price_raw"] == legacy_row["price"]
            assert next_row["price"] == pytest.approx(legacy_row["price"] / 1000)
            assert next_row["datetime"] == legacy_row["date"].strftime("%Y-%m-%d %H:%M:%S")
            assert next_row["volume"] == legacy_row["volume"]
            assert next_row["direction"] == legacy_row["direction"]

        next_range = next_client.bars_range(31, "00700", 20260701, EX_DATE)
        assert next_range
        try:
            legacy_range = legacy.get_history_instrument_bars_range(
                31,
                "00700",
                20260701,
                EX_DATE,
            )
        except Exception as exc:
            # tdxpy 0.2.7 constructs this parser with itself as the socket
            # client, so the public method raises an AttributeError wrapper.
            original = getattr(exc, "original_exception", exc)
            assert "attribute 'send'" in str(original)
        else:
            assert len(legacy_range) == len(next_range)
            for legacy_row, next_row in zip(
                legacy_range[:20], next_range[:20], strict=True
            ):
                assert next_row["datetime"] == legacy_row["datetime"]
                _assert_float_fields(next_row, legacy_row, ("open", "high", "low", "close"))
                assert next_row["settlement_price"] == pytest.approx(
                    legacy_row["settlementprice"]
                )

        next_futures = next_client.quotes(47, 3, 0, 2)
        assert len(next_futures) == 2
        legacy_list = tdxpy_exhq.TdxExHq_API(raise_exception=True, auto_retry=False)
        assert legacy_list.connect(EX_HOST, EX_PORT, time_out=5)
        try:
            legacy_futures = legacy_list.get_instrument_quote_list(47, 3, 0, 2)
        except Exception as exc:
            # tdxpy 0.2.7 returns the result list as the next record cursor and
            # raises TypeError. The next decoder advances exactly 300 bytes.
            original = getattr(exc, "original_exception", exc)
            assert "list" in str(original) and "int" in str(original)
        else:
            assert len(legacy_futures) == len(next_futures)
        finally:
            try:
                legacy_list.disconnect()
            except Exception:
                pass
    finally:
        next_client.close()
        try:
            legacy.disconnect()
        except Exception:
            # Some failing legacy parser paths close or corrupt their socket;
            # cleanup must not hide the protocol comparison result.
            pass
