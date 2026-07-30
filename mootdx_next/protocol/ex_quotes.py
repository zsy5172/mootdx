from __future__ import annotations

import math
import struct
from datetime import date
from typing import Any

from mootdx_next.errors import ProtocolDecodeError
from mootdx_next.errors import UnsupportedMarketError
from mootdx_next.interfaces import AbstractProtocol
from mootdx_next.models import ResponseEnvelope

# The wire layouts were independently cross-checked against live TDX ExHq
# responses and the MIT-licensed implementations in tdxpy and injoyai/tdx.
# Decoders here are rewritten to enforce complete record boundaries and avoid
# synthesizing a wrong trading date for after-midnight current-session data.

EX_MARKET_ROW_SIZE = 64
EX_INSTRUMENT_ROW_SIZE = 64
EX_QUOTE_BODY_STRUCT = struct.Struct("<fffffIIIIIIIIIfffffIIIIIfffffIIIII")
EX_BAR_BODY_STRUCT = struct.Struct("<ffffIIf")
EX_MINUTE_ROW_STRUCT = struct.Struct("<HffII")
EX_TRADE_ROW_STRUCT = struct.Struct("<HIIiH")
EX_RANGE_BAR_STRUCT = struct.Struct("<HHffffIIf")
EX_QUOTE_LIST_HK_STRUCT = struct.Struct("<IfffffIfIIfIIIIfffffIIIIIfffffIIIII")
EX_QUOTE_LIST_FUTURES_STRUCT = struct.Struct("<IfffffIIIIfIIfIfIIIIIIIIIfIIIIIIIII")


class ExQuoteProtocol(AbstractProtocol):
    def encode(self, api: str, **kwargs: Any) -> bytes:
        if api == "markets":
            return self.encode_markets()
        if api == "instrument_count":
            return self.encode_instrument_count()
        if api == "instruments":
            return self.encode_instruments(int(kwargs["start"]), int(kwargs["count"]))
        if api == "quote":
            return self.encode_quote(int(kwargs["market"]), str(kwargs["code"]))
        if api == "quotes":
            return self.encode_quote_list(
                int(kwargs["market"]), int(kwargs["start"]), int(kwargs["count"])
            )
        if api == "bars":
            return self.encode_bars(
                int(kwargs["category"]),
                int(kwargs["market"]),
                str(kwargs["code"]),
                int(kwargs["start"]),
                int(kwargs["count"]),
            )
        if api == "minute":
            return self.encode_minute(int(kwargs["market"]), str(kwargs["code"]))
        if api == "minutes":
            return self.encode_history_minutes(
                int(kwargs["market"]), str(kwargs["code"]), int(kwargs["date"])
            )
        if api == "transaction":
            return self.encode_transaction(
                int(kwargs["market"]),
                str(kwargs["code"]),
                int(kwargs["start"]),
                int(kwargs["count"]),
            )
        if api == "transactions":
            return self.encode_history_transactions(
                int(kwargs["market"]),
                str(kwargs["code"]),
                int(kwargs["date"]),
                int(kwargs["start"]),
                int(kwargs["count"]),
            )
        if api == "bars_range":
            return self.encode_bars_range(
                int(kwargs["market"]),
                str(kwargs["code"]),
                int(kwargs["start_date"]),
                int(kwargs["end_date"]),
            )
        raise NotImplementedError(f"unsupported extended protocol api: {api}")

    def decode(self, api: str, envelope: ResponseEnvelope, **kwargs: Any) -> object:
        body = envelope.body or b""
        if api == "markets":
            return self.decode_markets(body)
        if api == "instrument_count":
            return self.decode_instrument_count(body)
        if api == "instruments":
            return self.decode_instruments(body)
        if api == "quote":
            return self.decode_quote(body)
        if api == "quotes":
            return self.decode_quote_list(body, int(kwargs["category"]))
        if api == "bars":
            return self.decode_bars(body, int(kwargs["category"]))
        if api == "minute":
            return self.decode_minutes(body, history=False)
        if api == "minutes":
            return self.decode_minutes(body, history=True)
        if api == "transaction":
            return self.decode_transactions(body, market=int(kwargs["market"]), trading_date=None)
        if api == "transactions":
            return self.decode_transactions(
                body,
                market=int(kwargs["market"]),
                trading_date=int(kwargs["date"]),
            )
        if api == "bars_range":
            return self.decode_bars_range(body)
        raise NotImplementedError(f"unsupported extended protocol api: {api}")

    @staticmethod
    def encode_markets() -> bytes:
        return bytes.fromhex("01 02 48 69 00 01 02 00 02 00 f4 23")

    @staticmethod
    def encode_instrument_count() -> bytes:
        return bytes.fromhex("01 03 48 66 00 01 02 00 02 00 f0 23")

    def encode_instruments(self, start: int, count: int) -> bytes:
        self._validate_window(start, count, maximum=0xFFFF)
        payload = bytearray.fromhex("01 04 48 67 00 01 08 00 08 00 f5 23")
        payload.extend(struct.pack("<IH", start, count))
        return bytes(payload)

    def encode_quote(self, market: int, code: str) -> bytes:
        payload = bytearray.fromhex("01 01 08 02 02 01 0c 00 0c 00 fa 23")
        payload.extend(struct.pack("<B9s", self._market(market), self._code(code)))
        return bytes(payload)

    def encode_quote_list(self, market: int, start: int, count: int) -> bytes:
        self._validate_window(start, count, maximum=0xFFFF)
        payload = bytearray.fromhex("01 c1 06 0b 00 02 0b 00 0b 00 00 24")
        payload.extend(struct.pack("<BHHHH", self._market(market), 0, start, count, 1))
        return bytes(payload)

    def encode_bars(self, category: int, market: int, code: str, start: int, count: int) -> bytes:
        self._validate_category(category)
        self._validate_window(start, count, maximum=0xFFFF)
        payload = bytearray.fromhex("01 01 08 6a 01 01 16 00 16 00 ff 23")
        payload.extend(
            struct.pack(
                "<B9sHHIH",
                self._market(market),
                self._code(code),
                category,
                1,
                start,
                count,
            )
        )
        return bytes(payload)

    def encode_minute(self, market: int, code: str) -> bytes:
        payload = bytearray.fromhex("01 07 08 00 01 01 0c 00 0c 00 0b 24")
        payload.extend(struct.pack("<B9s", self._market(market), self._code(code)))
        return bytes(payload)

    def encode_history_minutes(self, market: int, code: str, trading_date: int) -> bytes:
        self._validate_date(trading_date)
        payload = bytearray.fromhex("01 01 30 00 01 01 10 00 10 00 0c 24")
        payload.extend(
            struct.pack("<IB9s", trading_date, self._market(market), self._code(code))
        )
        return bytes(payload)

    def encode_transaction(self, market: int, code: str, start: int, count: int) -> bytes:
        self._validate_window(start, count, maximum=0xFFFF)
        payload = bytearray.fromhex("01 01 08 00 03 01 12 00 12 00 fc 23")
        payload.extend(
            struct.pack("<B9siH", self._market(market), self._code(code), start, count)
        )
        return bytes(payload)

    def encode_history_transactions(
        self,
        market: int,
        code: str,
        trading_date: int,
        start: int,
        count: int,
    ) -> bytes:
        self._validate_date(trading_date)
        self._validate_window(start, count, maximum=0xFFFF)
        payload = bytearray.fromhex("01 01 30 00 02 01 16 00 16 00 06 24")
        payload.extend(
            struct.pack(
                "<IB9siH",
                trading_date,
                self._market(market),
                self._code(code),
                start,
                count,
            )
        )
        return bytes(payload)

    def encode_bars_range(
        self,
        market: int,
        code: str,
        start_date: int,
        end_date: int,
    ) -> bytes:
        self._validate_date(start_date)
        self._validate_date(end_date)
        if start_date > end_date:
            raise ValueError("start_date must be on or before end_date")
        payload = bytearray.fromhex("01 01 38 92 00 01 16 00 16 00 0d 24")
        payload.extend(struct.pack("<B9s", self._market(market), self._code(code)))
        payload.extend(bytes.fromhex("07 00"))
        payload.extend(struct.pack("<II", start_date, end_date))
        return bytes(payload)

    @staticmethod
    def decode_markets(body: bytes) -> list[dict[str, object]]:
        count = _read_count(body, "markets")
        _require_size(body, 2 + count * EX_MARKET_ROW_SIZE, "markets")
        rows: list[dict[str, object]] = []
        for index in range(count):
            pos = 2 + index * EX_MARKET_ROW_SIZE
            category, raw_name, market, raw_short_name = struct.unpack_from("<B32sB2s", body, pos)
            if category == 0 and market == 0:
                continue
            rows.append(
                {
                    "market": market,
                    "category": category,
                    "name": _gbk(raw_name),
                    "short_name": _gbk(raw_short_name),
                }
            )
        return rows

    @staticmethod
    def decode_instrument_count(body: bytes) -> int:
        if len(body) < 23:
            raise ProtocolDecodeError(f"instrument_count body too short: {len(body)}")
        try:
            (count,) = struct.unpack_from("<I", body, 19)
        except struct.error as exc:
            raise ProtocolDecodeError("failed to decode instrument_count") from exc
        return count

    @staticmethod
    def decode_instruments(body: bytes) -> list[dict[str, object]]:
        if len(body) < 6:
            raise ProtocolDecodeError(f"instruments body too short: {len(body)}")
        try:
            start, count = struct.unpack_from("<IH", body, 0)
        except struct.error as exc:
            raise ProtocolDecodeError("failed to decode instruments header") from exc
        expected_size = 6 + count * EX_INSTRUMENT_ROW_SIZE
        # Live ExHq nodes return one all-zero 64-byte placeholder for an empty
        # page. Their instrument_count includes reserved slots, so probing the
        # reported tail can legitimately produce this shape.
        if len(body) != expected_size:
            empty_page_with_padding = (
                count == 0
                and len(body) == 6 + EX_INSTRUMENT_ROW_SIZE
                and not any(body[6:])
            )
            if not empty_page_with_padding:
                _require_size(body, expected_size, "instruments")
        rows: list[dict[str, object]] = []
        for index in range(count):
            pos = 6 + index * EX_INSTRUMENT_ROW_SIZE
            category, market, _, raw_code, raw_name, raw_desc = struct.unpack_from(
                "<BB3s9s17s9s", body, pos
            )
            rows.append(
                {
                    "start": start + index,
                    "category": category,
                    "market": market,
                    "code": _gbk(raw_code),
                    "name": _gbk(raw_name),
                    "description": _gbk(raw_desc),
                }
            )
        return rows

    @staticmethod
    def decode_quote(body: bytes) -> dict[str, object] | None:
        if not body:
            return None
        expected = 14 + EX_QUOTE_BODY_STRUCT.size
        # Current ExHq nodes return a 150-byte compatibility extension after
        # the documented 150-byte quote record; older nodes return only the
        # record itself. The extension has no verified public semantics.
        if len(body) not in {expected, expected + 150}:
            raise ProtocolDecodeError(
                f"invalid quote body size: expected {expected} or {expected + 150} bytes, got {len(body)}"
            )
        market, raw_code = struct.unpack_from("<B9s", body, 0)
        values = EX_QUOTE_BODY_STRUCT.unpack_from(body, 14)
        floats = (*values[:5], *values[14:19], *values[24:29])
        if any(not math.isfinite(float(value)) for value in floats):
            raise ProtocolDecodeError("quote contains a non-finite price")

        row: dict[str, object] = {
            "market": market,
            "code": _gbk(raw_code),
            "pre_close": values[0],
            "open": values[1],
            "high": values[2],
            "low": values[3],
            "price": values[4],
            "open_interest": values[5],
            "volume": values[7],
            "current_volume": values[8],
            "inner_volume": values[10],
            "outer_volume": values[11],
            "position": values[13],
        }
        for level in range(5):
            row[f"bid{level + 1}"] = values[14 + level]
            row[f"bid_vol{level + 1}"] = values[19 + level]
            row[f"ask{level + 1}"] = values[24 + level]
            row[f"ask_vol{level + 1}"] = values[29 + level]
        return row

    @staticmethod
    def decode_bars(body: bytes, category: int) -> list[dict[str, object]]:
        if len(body) < 20:
            raise ProtocolDecodeError(f"bars body too short: {len(body)}")
        try:
            (count,) = struct.unpack_from("<H", body, 18)
        except struct.error as exc:
            raise ProtocolDecodeError("failed to decode bars count") from exc
        _require_size(body, 20 + count * 32, "bars")

        rows: list[dict[str, object]] = []
        pos = 20
        for index in range(count):
            year, month, day, hour, minute = _decode_bar_datetime(body, pos, category)
            values = EX_BAR_BODY_STRUCT.unpack_from(body, pos + 4)
            if any(not math.isfinite(float(value)) for value in (*values[:4], values[6])):
                raise ProtocolDecodeError(f"bars row {index} contains a non-finite price")
            (amount,) = struct.unpack_from("<f", body, pos + 20)
            rows.append(
                {
                    "datetime": f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}",
                    "year": year,
                    "month": month,
                    "day": day,
                    "hour": hour,
                    "minute": minute,
                    "open": values[0],
                    "high": values[1],
                    "low": values[2],
                    "close": values[3],
                    "position": values[4],
                    "trade": values[5],
                    "settlement_price": values[6],
                    "amount": amount,
                }
            )
            pos += 32
        return rows

    @staticmethod
    def decode_minutes(body: bytes, *, history: bool) -> list[dict[str, object]]:
        header_size = 20 if history else 12
        if len(body) < header_size:
            raise ProtocolDecodeError(f"minutes body too short: {len(body)}")
        try:
            (count,) = struct.unpack_from("<H", body, header_size - 2)
        except struct.error as exc:
            raise ProtocolDecodeError("failed to decode minutes count") from exc
        _require_size(body, header_size + count * EX_MINUTE_ROW_STRUCT.size, "minutes")

        rows: list[dict[str, object]] = []
        pos = header_size
        for index in range(count):
            raw_time, price, average_price, volume, open_interest = EX_MINUTE_ROW_STRUCT.unpack_from(
                body, pos
            )
            pos += EX_MINUTE_ROW_STRUCT.size
            hour, minute = divmod(raw_time, 60)
            if hour > 23 or not math.isfinite(price) or not math.isfinite(average_price):
                raise ProtocolDecodeError(f"invalid minutes row {index}")
            rows.append(
                {
                    "time": f"{hour:02d}:{minute:02d}",
                    "hour": hour,
                    "minute": minute,
                    "price": price,
                    "average_price": average_price,
                    "volume": volume,
                    "open_interest": open_interest,
                }
            )
        return rows

    @staticmethod
    def decode_transactions(
        body: bytes,
        *,
        market: int,
        trading_date: int | None,
    ) -> list[dict[str, object]]:
        if len(body) < 16:
            raise ProtocolDecodeError(f"transactions body too short: {len(body)}")
        try:
            (count,) = struct.unpack_from("<H", body, 14)
        except struct.error as exc:
            raise ProtocolDecodeError("failed to decode transactions count") from exc
        expected_size = 16 + count * EX_TRADE_ROW_STRUCT.size
        if len(body) < expected_size or any(body[expected_size:]):
            raise ProtocolDecodeError(
                f"invalid transactions body size: expected {expected_size} bytes plus zero padding, "
                f"got {len(body)}"
            )

        date_prefix = None
        if trading_date is not None:
            year, remainder = divmod(trading_date, 10000)
            month, day = divmod(remainder, 100)
            _validated_date(year, month, day)
            date_prefix = f"{year:04d}-{month:02d}-{day:02d} "

        rows: list[dict[str, object]] = []
        pos = 16
        for index in range(count):
            raw_time, raw_price, volume, position_change, nature = EX_TRADE_ROW_STRUCT.unpack_from(
                body, pos
            )
            pos += EX_TRADE_ROW_STRUCT.size
            hour, minute = divmod(raw_time, 60)
            second = nature % 10000
            if second > 59:
                second = 0
            if hour > 23:
                raise ProtocolDecodeError(f"invalid transactions row {index}")
            direction, nature_name = _trade_nature(market, nature, volume, position_change)
            time_value = f"{hour:02d}:{minute:02d}:{second:02d}"
            row: dict[str, object] = {
                "time": time_value,
                "hour": hour,
                "minute": minute,
                "second": second,
                "price": raw_price / 1000.0,
                "price_raw": raw_price,
                "volume": volume,
                "position_change": position_change,
                "nature": nature,
                "nature_mark": nature // 10000,
                "nature_value": nature % 10000,
                "nature_name": nature_name,
                "direction": direction,
            }
            if date_prefix is not None:
                row["datetime"] = date_prefix + time_value
            rows.append(row)
        return rows

    @staticmethod
    def decode_bars_range(body: bytes) -> list[dict[str, object]]:
        if len(body) < 14:
            raise ProtocolDecodeError(f"bars_range body too short: {len(body)}")
        try:
            (count,) = struct.unpack_from("<H", body, 12)
        except struct.error as exc:
            raise ProtocolDecodeError("failed to decode bars_range count") from exc
        _require_size(body, 14 + count * EX_RANGE_BAR_STRUCT.size, "bars_range")

        rows: list[dict[str, object]] = []
        pos = 14
        for index in range(count):
            values = EX_RANGE_BAR_STRUCT.unpack_from(body, pos)
            pos += EX_RANGE_BAR_STRUCT.size
            year, month, day = _decode_compressed_date(values[0])
            hour, minute = divmod(values[1], 60)
            if hour > 23 or any(not math.isfinite(float(value)) for value in (*values[2:6], values[8])):
                raise ProtocolDecodeError(f"invalid bars_range row {index}")
            rows.append(
                {
                    "datetime": f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}",
                    "year": year,
                    "month": month,
                    "day": day,
                    "hour": hour,
                    "minute": minute,
                    "open": values[2],
                    "high": values[3],
                    "low": values[4],
                    "close": values[5],
                    "position": values[6],
                    "trade": values[7],
                    "settlement_price": values[8],
                }
            )
        return rows

    @staticmethod
    def decode_quote_list(body: bytes, category: int) -> list[dict[str, object]]:
        if category not in {2, 3}:
            raise UnsupportedMarketError("quote list currently supports category 2 (HK) and 3 (futures)")
        count = _read_count(body, "quote_list")
        _require_size(body, 2 + count * 300, "quote_list")
        rows: list[dict[str, object]] = []
        for index in range(count):
            pos = 2 + index * 300
            market, raw_code = struct.unpack_from("<B9s", body, pos)
            data_pos = pos + 10
            if category == 2:
                rows.append(_decode_hk_quote_list_row(body, data_pos, market, _gbk(raw_code)))
            else:
                rows.append(_decode_futures_quote_list_row(body, data_pos, market, _gbk(raw_code)))
        return rows

    @staticmethod
    def _market(market: int) -> int:
        if not 0 <= market <= 0xFF:
            raise UnsupportedMarketError(f"extended market must be between 0 and 255: {market}")
        return market

    @staticmethod
    def _code(code: str) -> bytes:
        if not isinstance(code, str) or not code.strip():
            raise ValueError("extended symbol cannot be blank")
        try:
            encoded = code.strip().encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("extended symbol must contain ASCII characters") from exc
        if len(encoded) > 9 or b"\x00" in encoded:
            raise ValueError("extended symbol must fit within 9 ASCII bytes")
        return encoded

    @staticmethod
    def _validate_window(start: int, count: int, *, maximum: int) -> None:
        if start < 0 or start > 0x7FFFFFFF:
            raise ValueError("start must be between 0 and 2147483647")
        if count <= 0 or count > maximum:
            raise ValueError(f"count must be between 1 and {maximum}")

    @staticmethod
    def _validate_category(category: int) -> None:
        if not 0 <= category <= 11:
            raise ValueError("category must be between 0 and 11")

    @staticmethod
    def _validate_date(value: int) -> None:
        year, remainder = divmod(value, 10000)
        month, day = divmod(remainder, 100)
        _validated_date(year, month, day)


def _read_count(body: bytes, api: str) -> int:
    if len(body) < 2:
        raise ProtocolDecodeError(f"{api} body too short: {len(body)}")
    try:
        return int(struct.unpack_from("<H", body, 0)[0])
    except struct.error as exc:
        raise ProtocolDecodeError(f"failed to decode {api} count") from exc


def _require_size(body: bytes, expected: int, api: str) -> None:
    if len(body) != expected:
        raise ProtocolDecodeError(
            f"invalid {api} body size: expected {expected} bytes, got {len(body)}"
        )


def _gbk(value: bytes) -> str:
    return value.split(b"\x00", 1)[0].decode("gbk", "ignore")


def _validated_date(year: int, month: int, day: int) -> None:
    try:
        date(year, month, day)
    except ValueError as exc:
        raise ProtocolDecodeError(f"invalid extended-market date: {year:04d}-{month:02d}-{day:02d}") from exc


def _decode_compressed_date(value: int) -> tuple[int, int, int]:
    year = value // 2048 + 2004
    remainder = value % 2048
    month, day = divmod(remainder, 100)
    _validated_date(year, month, day)
    return year, month, day


def _decode_bar_datetime(body: bytes, pos: int, category: int) -> tuple[int, int, int, int, int]:
    if category < 4 or category in {7, 8}:
        compressed_date, raw_time = struct.unpack_from("<HH", body, pos)
        year, month, day = _decode_compressed_date(compressed_date)
        hour, minute = divmod(raw_time, 60)
    else:
        (raw_date,) = struct.unpack_from("<I", body, pos)
        year, remainder = divmod(raw_date, 10000)
        month, day = divmod(remainder, 100)
        _validated_date(year, month, day)
        hour, minute = 15, 0
    if hour > 23:
        raise ProtocolDecodeError(f"invalid extended-market time: {hour:02d}:{minute:02d}")
    return year, month, day, hour, minute


def _trade_nature(market: int, nature: int, volume: int, position_change: int) -> tuple[int, str]:
    if market in {31, 48}:
        if nature == 0:
            return 1, "B"
        if nature == 256:
            return -1, "S"
        return 0, ""

    value = nature // 10000
    if value == 0:
        if position_change > 0:
            return 1, "双开" if volume == position_change else "多开"
        if position_change == 0:
            return 1, "多换"
        return 1, "双平" if volume == -position_change else "空平"
    if value == 1:
        if position_change > 0:
            return -1, "双开" if volume == position_change else "空开"
        if position_change == 0:
            return -1, "空换"
        return -1, "双平" if volume == -position_change else "多平"
    if position_change > 0:
        return 0, "双开" if volume == position_change else "开仓"
    if position_change < 0:
        return 0, "双平" if volume == -position_change else "平仓"
    return 0, "换手"


def _decode_hk_quote_list_row(
    body: bytes,
    pos: int,
    market: int,
    code: str,
) -> dict[str, object]:
    values = EX_QUOTE_LIST_HK_STRUCT.unpack_from(body, pos)
    row: dict[str, object] = {
        "market": market,
        "code": code,
        "activity": values[0],
        "pre_close": values[1],
        "open": values[2],
        "high": values[3],
        "low": values[4],
        "price": values[5],
        "reference_bid": values[7],
        "volume": values[8],
        "current_volume": values[9],
        "amount": values[10],
        "inner_volume": values[13],
        "outer_volume": values[14],
    }
    for level in range(5):
        row[f"bid{level + 1}"] = values[15 + level]
        row[f"bid_vol{level + 1}"] = values[20 + level]
        row[f"ask{level + 1}"] = values[25 + level]
        row[f"ask_vol{level + 1}"] = values[30 + level]
    return row


def _decode_futures_quote_list_row(
    body: bytes,
    pos: int,
    market: int,
    code: str,
) -> dict[str, object]:
    values = EX_QUOTE_LIST_FUTURES_STRUCT.unpack_from(body, pos)
    return {
        "market": market,
        "code": code,
        "trade_count": values[0],
        "pre_settlement": values[1],
        "open": values[2],
        "high": values[3],
        "low": values[4],
        "price": values[5],
        "open_interest": values[6],
        "volume": values[8],
        "current_volume": values[9],
        "amount": values[10],
        "inner_volume": values[11],
        "outer_volume": values[12],
        "position": values[14],
        "bid1": values[15],
        "bid_vol1": values[20],
        "ask1": values[25],
        "ask_vol1": values[30],
    }
