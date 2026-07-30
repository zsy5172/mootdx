from __future__ import annotations

import struct

import pytest

from mootdx_next.errors import ProtocolDecodeError
from mootdx_next.errors import UnsupportedMarketError
from mootdx_next.models import ResponseEnvelope
from mootdx_next.protocol import ExQuoteProtocol
from mootdx_next.protocol.ex_quotes import EX_QUOTE_BODY_STRUCT
from mootdx_next.protocol.ex_quotes import EX_QUOTE_LIST_FUTURES_STRUCT
from mootdx_next.protocol.ex_quotes import EX_QUOTE_LIST_HK_STRUCT


def _decode(protocol: ExQuoteProtocol, api: str, body: bytes, **kwargs: object):
    return protocol.decode(api, ResponseEnvelope(body=body), **kwargs)


def _compressed_date(year: int, month: int, day: int) -> int:
    return (year - 2004) * 2048 + month * 100 + day


def test_encode_extended_protocol_requests() -> None:
    protocol = ExQuoteProtocol()

    assert protocol.encode("markets") == bytes.fromhex("01 02 48 69 00 01 02 00 02 00 f4 23")
    assert protocol.encode("instrument_count")[-2:] == bytes.fromhex("f0 23")
    assert protocol.encode("instruments", start=7, count=11)[-6:] == struct.pack("<IH", 7, 11)
    assert protocol.encode("quote", market=31, code="00700")[-10:] == struct.pack(
        "<B9s", 31, b"00700"
    )
    assert protocol.encode(
        "bars", category=9, market=31, code="00700", start=2, count=7
    )[-20:] == struct.pack("<B9sHHIH", 31, b"00700", 9, 1, 2, 7)
    assert protocol.encode(
        "transactions", market=31, code="00700", date=20260729, start=3, count=5
    )[-20:] == struct.pack("<IB9siH", 20260729, 31, b"00700", 3, 5)


@pytest.mark.parametrize(
    ("api", "kwargs", "message"),
    [
        ("quote", {"market": 256, "code": "00700"}, "between 0 and 255"),
        ("quote", {"market": 31, "code": ""}, "blank"),
        ("quote", {"market": 31, "code": "TOO-LONG-10"}, "9 ASCII"),
        (
            "bars",
            {"category": 12, "market": 31, "code": "00700", "start": 0, "count": 1},
            "between 0 and 11",
        ),
        (
            "bars_range",
            {"market": 31, "code": "00700", "start_date": 20260730, "end_date": 20260729},
            "on or before",
        ),
    ],
)
def test_encode_rejects_invalid_arguments(api: str, kwargs: dict[str, object], message: str) -> None:
    with pytest.raises((ValueError, ProtocolDecodeError, UnsupportedMarketError), match=message):
        ExQuoteProtocol().encode(api, **kwargs)


def test_decode_markets_count_and_instruments() -> None:
    protocol = ExQuoteProtocol()
    market_row = struct.pack(
        "<B32sB2s28s",
        2,
        "香港主板".encode("gbk").ljust(32, b"\x00"),
        31,
        b"KH",
        b"\x00" * 28,
    )
    markets = _decode(protocol, "markets", struct.pack("<H", 1) + market_row)
    assert markets == [{"market": 31, "category": 2, "name": "香港主板", "short_name": "KH"}]

    count_body = bytearray(b"\x00" * 23)
    struct.pack_into("<I", count_body, 19, 140727)
    assert _decode(protocol, "instrument_count", bytes(count_body)) == 140727

    instrument_row = struct.pack(
        "<BB3s9s17s9s24s",
        2,
        31,
        b"\x00" * 3,
        b"00700\x00\x00\x00\x00",
        "腾讯控股".encode("gbk").ljust(17, b"\x00"),
        b"desc\x00\x00\x00\x00\x00",
        b"\x00" * 24,
    )
    instruments = _decode(
        protocol,
        "instruments",
        struct.pack("<IH", 123, 1) + instrument_row,
    )
    assert instruments == [
        {
            "start": 123,
            "category": 2,
            "market": 31,
            "code": "00700",
            "name": "腾讯控股",
            "description": "desc",
        }
    ]

    empty_page = struct.pack("<IH", 140699, 0) + b"\x00" * 64
    assert _decode(protocol, "instruments", empty_page) == []

    with pytest.raises(ProtocolDecodeError):
        _decode(protocol, "instruments", struct.pack("<IH", 140699, 0) + b"\x01" * 64)


def test_decode_single_quote_and_compatibility_extension() -> None:
    values = (
        460.0,
        461.0,
        470.0,
        455.0,
        466.4,
        12,
        0,
        3456,
        78,
        0,
        1200,
        2200,
        0,
        999,
        *[460.0 + level for level in range(5)],
        *[100 + level for level in range(5)],
        *[466.5 + level for level in range(5)],
        *[200 + level for level in range(5)],
    )
    body = struct.pack("<B9s4s", 31, b"00700", b"\x00" * 4) + EX_QUOTE_BODY_STRUCT.pack(
        *values
    )
    protocol = ExQuoteProtocol()

    for response in (body, body + b"\x00" * 150):
        quote = _decode(protocol, "quote", response)
        assert quote["market"] == 31
        assert quote["code"] == "00700"
        assert quote["price"] == pytest.approx(466.4)
        assert quote["open_interest"] == 12
        assert quote["position"] == 999
        assert quote["bid1"] == pytest.approx(460.0)
        assert quote["ask_vol5"] == 204


def test_decode_bars_minute_and_daily_time_encodings() -> None:
    protocol = ExQuoteProtocol()
    body_values = struct.pack("<ffffIIf", 10.0, 11.0, 9.0, 10.5, 123, 456, 10.2)
    minute_body = b"\x00" * 18 + struct.pack("<H", 1)
    minute_body += struct.pack("<HH", _compressed_date(2026, 7, 29), 9 * 60 + 35)
    minute_body += body_values
    minute_rows = _decode(protocol, "bars", minute_body, category=0)
    assert minute_rows[0]["datetime"] == "2026-07-29 09:35"
    assert minute_rows[0]["trade"] == 456

    daily_body = b"\x00" * 18 + struct.pack("<H", 1)
    daily_body += struct.pack("<I", 20260729) + body_values
    daily_rows = _decode(protocol, "bars", daily_body, category=9)
    assert daily_rows[0]["datetime"] == "2026-07-29 15:00"
    assert daily_rows[0]["settlement_price"] == pytest.approx(10.2)


def test_decode_current_and_historical_minutes_without_inventing_date() -> None:
    row = struct.pack("<HffII", 9 * 60 + 30, 466.4, 465.8, 100, 200)
    protocol = ExQuoteProtocol()
    current = _decode(protocol, "minute", b"\x00" * 10 + struct.pack("<H", 1) + row)
    history = _decode(protocol, "minutes", b"\x00" * 18 + struct.pack("<H", 1) + row)

    assert current == history
    assert current[0] == {
        "time": "09:30",
        "hour": 9,
        "minute": 30,
        "price": pytest.approx(466.4),
        "average_price": pytest.approx(465.8),
        "volume": 100,
        "open_interest": 200,
    }
    assert "date" not in current[0]


def test_decode_transactions_scales_real_price_and_dates_only_history() -> None:
    row = struct.pack("<HIIiH", 15 * 60 + 42, 466400, 300, 0, 512)
    body = b"\x00" * 14 + struct.pack("<H", 1) + row
    protocol = ExQuoteProtocol()

    current = _decode(protocol, "transaction", body, market=31)
    history = _decode(protocol, "transactions", body, market=31, date=20260729)

    assert current[0]["price"] == pytest.approx(466.4)
    assert current[0]["price_raw"] == 466400
    assert "datetime" not in current[0]
    assert history[0]["datetime"] == "2026-07-29 15:42:00"


def test_decode_range_bars_and_quote_lists() -> None:
    protocol = ExQuoteProtocol()
    range_row = struct.pack(
        "<HHffffIIf",
        _compressed_date(2026, 7, 29),
        9 * 60 + 31,
        460.0,
        467.0,
        459.0,
        466.4,
        123,
        456,
        465.0,
    )
    rows = _decode(protocol, "bars_range", b"\x00" * 12 + struct.pack("<H", 1) + range_row)
    assert rows[0]["datetime"] == "2026-07-29 09:31"
    assert rows[0]["close"] == pytest.approx(466.4)

    hk_values = (
        1,
        460.0,
        461.0,
        470.0,
        455.0,
        466.4,
        0,
        466.3,
        3456,
        78,
        123456.0,
        0,
        0,
        1200,
        2200,
        *[460.0 + level for level in range(5)],
        *[100 + level for level in range(5)],
        *[466.5 + level for level in range(5)],
        *[200 + level for level in range(5)],
    )
    hk_body = struct.pack("<H", 1) + struct.pack("<B9s", 31, b"00700")
    hk_body += EX_QUOTE_LIST_HK_STRUCT.pack(*hk_values) + b"\x00" * 150
    hk = _decode(protocol, "quotes", hk_body, category=2)
    assert hk[0]["price"] == pytest.approx(466.4)
    assert hk[0]["bid1"] == pytest.approx(460.0)
    assert hk[0]["ask_vol5"] == 204

    futures_values = [0] * 35
    futures_values[0] = 88
    for index, value in enumerate((7400.0, 7410.0, 7500.0, 7390.0, 7480.0), start=1):
        futures_values[index] = value
    futures_values[6] = 11
    futures_values[8] = 1000
    futures_values[9] = 12
    futures_values[10] = 123456.0
    futures_values[11] = 400
    futures_values[12] = 600
    futures_values[14] = 999
    futures_values[15] = 7479.8
    futures_values[20] = 15
    futures_values[25] = 7480.2
    futures_values[30] = 18
    futures_body = struct.pack("<H", 1) + struct.pack("<B9s", 47, b"IF2608")
    futures_body += EX_QUOTE_LIST_FUTURES_STRUCT.pack(*futures_values) + b"\x00" * 150
    futures = _decode(protocol, "quotes", futures_body, category=3)
    assert futures[0]["price"] == pytest.approx(7480.0)
    assert futures[0]["bid1"] == pytest.approx(7479.8)
    assert futures[0]["bid_vol1"] == 15
    assert futures[0]["ask1"] == pytest.approx(7480.2)
    assert futures[0]["ask_vol1"] == 18


@pytest.mark.parametrize(
    ("api", "body", "kwargs"),
    [
        ("markets", struct.pack("<H", 1) + b"\x00" * 63, {}),
        ("instruments", struct.pack("<IH", 0, 1) + b"\x00" * 63, {}),
        ("quote", b"\x00" * 149, {}),
        ("bars", b"\x00" * 18 + struct.pack("<H", 1), {"category": 9}),
        ("minutes", b"\x00" * 19, {}),
        ("transactions", b"\x00" * 15, {"market": 31, "date": 20260729}),
        ("bars_range", b"\x00" * 13, {}),
        ("quotes", struct.pack("<H", 1) + b"\x00" * 299, {"category": 2}),
    ],
)
def test_decoders_reject_truncated_records(
    api: str,
    body: bytes,
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ProtocolDecodeError):
        _decode(ExQuoteProtocol(), api, body, **kwargs)
