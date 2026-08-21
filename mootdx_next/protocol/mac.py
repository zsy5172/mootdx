"""TDX MAC 应用协议编解码。

MAC 是通达信在标准行情协议之外使用的一组应用消息。请求采用 10 字节
``<BIBHH`` 头，后跟 2 字节消息 ID；响应仍复用 next 引擎的标准响应帧，
由 transport 完成压缩处理。
"""

from __future__ import annotations

import json
import struct
from datetime import date, datetime, time
from typing import Any

from mootdx_next.errors import ProtocolDecodeError, ProtocolEncodeError
from mootdx_next.interfaces import AbstractProtocol
from mootdx_next.mac.types import (
    MacAdjust,
    MacBoardType,
    MacField,
    MacPeriod,
    MacSortOrder,
    MacSortType,
    active_mac_fields,
    mac_field_value,
    normalize_mac_fields,
)
from mootdx_next.models import ResponseEnvelope

MAC_HEADER = struct.Struct("<BIBHH")
MAX_MAC_QUOTES = 80
MAX_MAC_BOARD_PAGE = 80
MAX_MAC_KLINE_PAGE = 700
MAX_MAC_TRANSACTION_PAGE = 1000


def _pad(value: str, size: int) -> bytes:
    raw = str(value).encode("gbk")
    if len(raw) > size:
        raise ProtocolEncodeError(f"MAC value exceeds {size} bytes: {value!r}")
    return raw + b"\x00" * (size - len(raw))


def build_mac_request(message_id: int, body: bytes, *, head_flag: int = 0x1C) -> bytes:
    if not 0 <= int(message_id) <= 0xFFFF:
        raise ProtocolEncodeError("MAC message_id must fit uint16")
    inner = struct.pack("<H", int(message_id)) + body
    return MAC_HEADER.pack(int(head_flag), 0, 1, len(inner), len(inner)) + inner


def _symbol(value: object, *, allow_extended: bool = False) -> tuple[int, str]:
    if isinstance(value, (tuple, list)) and len(value) == 2:
        market, code = int(value[0]), str(value[1]).strip()
    else:
        raw = str(value).strip()
        if raw[:2].lower() == "sh":
            market, code = 1, raw[2:].lstrip(".#")
        elif raw[:2].lower() == "bj":
            market, code = 2, raw[2:].lstrip(".#")
        elif raw[:2].lower() == "sz":
            market, code = 0, raw[2:].lstrip(".#")
        elif raw.startswith(("920", "43", "82", "83", "87", "89")):
            market, code = 2, raw
        elif raw.startswith(("88", "6", "5", "9")):
            market, code = 1, raw
        elif raw.startswith(("0", "1", "2", "3")):
            market, code = 0, raw
        else:
            raise ProtocolEncodeError(f"cannot infer MAC market from symbol: {value!r}")
    if (not allow_extended and market not in {0, 1, 2}) or not 0 <= market <= 0xFF or not code or len(code) > 22:
        raise ProtocolEncodeError(f"MAC symbol must be market + six-digit code: {value!r}")
    return market, code


def _ymd(value: object | None) -> int:
    if value is None:
        return 0
    if isinstance(value, (datetime, date)):
        return value.year * 10000 + value.month * 100 + value.day
    raw = str(value).replace("-", "").strip()
    if len(raw) != 8 or not raw.isdigit():
        raise ProtocolEncodeError(f"invalid MAC date: {value!r}")
    return int(raw)


def _decode_text(data: bytes) -> str:
    return data.split(b"\x00", 1)[0].decode("gbk", errors="replace")


def _json_body(body: bytes, offset: int = 27) -> list[Any]:
    if len(body) < offset:
        return []
    try:
        value = json.loads(body[offset:].decode("gbk", errors="replace"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolDecodeError("invalid MAC JSON response") from exc
    return value if isinstance(value, list) else []


def _describe_unusual(unusual_type: int, data: bytes) -> tuple[str, str]:
    """Decode the compact value payload used by 0x1237 descriptions."""

    if len(data) < 13:
        return "", ""
    v1, v2, v3, v4 = struct.unpack_from("<B2fI", data)
    if unusual_type == 0x03:
        return f"主力{'买入' if v1 == 0 else '卖出'}", f"{v2:.2f}/{v3:.2f}"
    if unusual_type in {0x04, 0x06, 0x07, 0x08, 0x09}:
        names = {0x04: "加速拉升", 0x06: "低位反弹", 0x07: "高位回落", 0x08: "撑杆跳高", 0x09: "平台跳水"}
        return names[unusual_type], f"{v2 * 100:.2f}%"
    if unusual_type == 0x0A:
        return f"单笔冲{'跌' if v2 < 0 else '涨'}", f"{abs(v2) * 100:.2f}%"
    if unusual_type == 0x05:
        return "加速下跌", ""
    if unusual_type == 0x0B:
        direction = "平" if v3 == 0 else "跌" if v3 < 0 else "涨"
        return f"区间放量{direction}", f"{v2:.1f}倍" + ("" if v3 == 0 else f"{v3 * 100:.2f}%")
    if unusual_type == 0x0C:
        return "区间缩量", ""
    if unusual_type == 0x10:
        return "大单托盘", f"{v4:.2f}/{v3:.2f}"
    if unusual_type == 0x11:
        return "大单压盘", f"{v2:.2f}/{v3:.2f}"
    if unusual_type == 0x12:
        return "大单锁盘", ""
    if unusual_type == 0x13:
        return "竞价试买", f"{v2:.2f}/{v3:.2f}"
    if unusual_type == 0x14:
        direction = "涨" if v1 == 0 else "跌"
        # Type 0x14 uses a subtype byte followed by two floats instead of the
        # generic v2/v3/v4 payload.  Reading it as the generic layout turns
        # normal limit-up/down records into huge nonsensical values.
        subtype, value_1, value_2 = struct.unpack_from("<Bff", data, 1)
        descriptions = {
            0x01: f"逼近{direction}停",
            0x02: f"封{direction}停板",
            0x04: f"封{direction}大减",
            0x05: f"打开{direction}停",
        }
        return descriptions.get(subtype, f"涨跌停({direction})"), f"{value_1:.2f}/{value_2:.2f}"
    return f"异动类型{unusual_type:#04x}", ""


class MacProtocol(AbstractProtocol):
    """A 股 MAC 协议。``head_flag`` 可由 MAC EX 子类改为 ``0x01``。"""

    head_flag = 0x1C

    def encode(self, api: str, **kwargs: Any) -> bytes:
        if api in {"mac_quotes", "mac_ex_quotes"}:
            stocks = [
                _symbol(item, allow_extended=bool(kwargs.get("extended")) or api == "mac_ex_quotes")
                for item in kwargs["symbols"]
            ]
            if not stocks or len(stocks) > MAX_MAC_QUOTES:
                raise ProtocolEncodeError("MAC quotes supports 1..80 symbols")
            body = bytearray(normalize_mac_fields(kwargs.get("fields")).bitmap())
            body += struct.pack("<H", len(stocks))
            for market, code in stocks:
                body += struct.pack("<H22s", market, _pad(code, 22))
            return build_mac_request(0x122B, bytes(body), head_flag=self.head_flag)
        if api in {"mac_quotes_list", "mac_board_members"}:
            board_code = int(kwargs["board_code"])
            full_bitmap = normalize_mac_fields(kwargs.get("fields")).bitmap()
            fields = full_bitmap[:16]
            excludes = sum(int(item) for item in (kwargs.get("exclude_flags") or ()))
            body = struct.pack(
                "<I9xHIHBB",
                board_code,
                int(kwargs.get("sort_by", MacSortType.CHANGE_PCT)),
                int(kwargs.get("start", 0)), int(kwargs.get("count", MAX_MAC_BOARD_PAGE)),
                int(kwargs.get("sort_order", MacSortOrder.NONE)), 0,
            ) + fields + bytes(
                (full_bitmap[16], full_bitmap[17] | excludes, full_bitmap[18], full_bitmap[19] | 1)
            )
            return build_mac_request(0x122C, body, head_flag=self.head_flag)
        if api == "mac_board_list":
            body = struct.pack(
                "<HHBBHH8x", int(kwargs.get("count", 150)), int(kwargs.get("board_type", MacBoardType.ALL)),
                0, 0, int(kwargs.get("start", 0)), 1,
            )
            return build_mac_request(0x1231, body, head_flag=self.head_flag)
        if api in {"mac_belong_board", "mac_capital_flow"}:
            market, code = _symbol(
                kwargs["symbol"],
                allow_extended=bool(kwargs.get("extended")) or api.startswith("mac_ex_"),
            )
            query = b"Stock_GLHQ" if api == "mac_belong_board" else b"Stock_ZJLX"
            flag = 1 if api == "mac_belong_board" else 2
            body = struct.pack("<H8s16x21s", market, _pad(code, 8), query)
            return build_mac_request(0x1218, body, head_flag=flag)
        if api == "mac_symbol_info":
            market, code = _symbol(
                kwargs["symbol"],
                allow_extended=bool(kwargs.get("extended")) or api.startswith("mac_ex_"),
            )
            return build_mac_request(
                0x122A,
                struct.pack("<H22sI12x", market, _pad(code, 22), 1),
                head_flag=self.head_flag,
            )
        if api == "mac_bars":
            market, code = _symbol(
                kwargs["symbol"],
                allow_extended=bool(kwargs.get("extended")) or api.startswith("mac_ex_"),
            )
            body = struct.pack(
                "<H22sHH I HH bbb bH4s",
                market, _pad(code, 22), int(kwargs.get("frequency", MacPeriod.DAY)),
                int(kwargs.get("times", 1)), int(kwargs.get("start", 0)), int(kwargs.get("count", MAX_MAC_KLINE_PAGE)),
                int(kwargs.get("adjust", MacAdjust.NONE)), 1, 1, 0, 1, 0, b"",
            )
            return build_mac_request(0x122E, body, head_flag=self.head_flag)
        if api == "mac_tick_chart":
            market, code = _symbol(
                kwargs["symbol"],
                allow_extended=bool(kwargs.get("extended")) or api.startswith("mac_ex_"),
            )
            body = struct.pack(
                "<H22sI5H", market, _pad(code, 22), _ymd(kwargs.get("date")), 1, 0, 0, 0, 0
            )
            return build_mac_request(0x122D, body, head_flag=self.head_flag)
        if api == "mac_tick_charts":
            market, code = _symbol(
                kwargs["symbol"],
                allow_extended=bool(kwargs.get("extended")) or api.startswith("mac_ex_"),
            )
            body = struct.pack(
                "<H22sIHH6x", market, _pad(code, 22), _ymd(kwargs.get("date")), int(kwargs.get("days", 5)), 1
            )
            return build_mac_request(0x123E, body, head_flag=self.head_flag)
        if api == "mac_chart_sampling":
            market, code = _symbol(
                kwargs["symbol"],
                allow_extended=bool(kwargs.get("extended")) or api.startswith("mac_ex_"),
            )
            return build_mac_request(
                0x254D,
                struct.pack("<H22sHH9x", market, _pad(code, 22), 1, 20),
                head_flag=self.head_flag,
            )
        if api == "mac_transactions":
            market, code = _symbol(kwargs["symbol"], allow_extended=bool(kwargs.get("extended")) or api.startswith("mac_ex_"))
            body = struct.pack("<H22sIIH10x", market, _pad(code, 22), _ymd(kwargs.get("date")), int(kwargs.get("start", 0)), int(kwargs.get("count", MAX_MAC_TRANSACTION_PAGE)))
            return build_mac_request(0x122F, body, head_flag=self.head_flag)
        if api == "mac_auction":
            market, code = _symbol(kwargs["symbol"], allow_extended=bool(kwargs.get("extended")) or api.startswith("mac_ex_"))
            body = struct.pack("<H22sII10x", market, _pad(code, 22), int(kwargs.get("start", 0)), int(kwargs.get("count", 500)))
            return build_mac_request(0x123D, body, head_flag=self.head_flag)
        if api == "mac_unusual":
            body = struct.pack("<HH2xH2xH5H", int(kwargs["market"]), int(kwargs.get("start", 0)), int(kwargs.get("count", 600)), 1, 200, 30, 40, 50, 200)
            return build_mac_request(0x1237, body, head_flag=self.head_flag)
        if api == "mac_server_info":
            body = bytes.fromhex("04002d31") + b"\x00" * 8 + b"\x00\x27\x06\x0e" + b"\x00" * 52
            return build_mac_request(0x120F, body, head_flag=self.head_flag)
        if api == "mac_kline_offset":
            return build_mac_request(0x124A, struct.pack("<II5x", int(kwargs.get("offset", 0)), int(kwargs.get("count", 128000))), head_flag=self.head_flag)
        if api == "mac_file_meta":
            return build_mac_request(0x1215, struct.pack("<I", int(kwargs.get("offset", 0))) + _pad(kwargs["filename"], 70) + b"\x00" * 30, head_flag=self.head_flag)
        if api == "mac_file_chunk":
            body = struct.pack("<III", int(kwargs.get("index", 1)), int(kwargs.get("offset", 0)), int(kwargs.get("size", 30000))) + _pad(kwargs["filename"], 70) + b"\x00" * 30
            return build_mac_request(0x1217, body, head_flag=self.head_flag)
        if api == "mac_goods_list":
            return build_mac_request(0x2562, struct.pack("<HII", int(kwargs["market"]), int(kwargs.get("start", 0)), int(kwargs.get("count", 600))), head_flag=self.head_flag)
        raise NotImplementedError(f"unsupported MAC protocol api: {api}")

    def decode(self, api: str, envelope: ResponseEnvelope, **kwargs: Any) -> object:
        body = envelope.body or b""
        if api == "mac_ex_login":
            return len(body) >= 2
        if api in {"mac_quotes", "mac_quotes_list", "mac_board_members", "mac_ex_quotes"}:
            return self._decode_quotes(body, board=api == "mac_board_members")
        if api == "mac_board_list":
            return self._decode_board_list(body)
        if api == "mac_belong_board":
            return self._decode_belong(body)
        if api == "mac_capital_flow":
            return self._decode_capital_flow(body)
        if api == "mac_symbol_info":
            return self._decode_symbol_info(body)
        if api == "mac_bars":
            return self._decode_bars(body, int(kwargs.get("frequency", MacPeriod.DAY)))
        if api == "mac_tick_chart":
            return self._decode_tick_chart(body)
        if api == "mac_tick_charts":
            return self._decode_tick_charts(body)
        if api == "mac_chart_sampling":
            return self._decode_sampling(body)
        if api == "mac_transactions":
            return self._decode_transactions(body)
        if api == "mac_auction":
            return self._decode_auction(body)
        if api == "mac_unusual":
            return self._decode_unusual(body)
        if api == "mac_server_info":
            return self._decode_server_info(body)
        if api == "mac_kline_offset":
            return self._decode_kline_offset(body)
        if api == "mac_file_meta":
            return self._decode_file_meta(body)
        if api == "mac_file_chunk":
            return body[8:] if len(body) >= 8 else b""
        if api == "mac_goods_list":
            return self._decode_goods(body)
        raise NotImplementedError(f"unsupported MAC protocol api: {api}")

    @staticmethod
    def _decode_quotes(body: bytes, *, board: bool = False) -> list[dict[str, object]]:
        if len(body) < 26:
            return []
        bitmap = body[:20]
        try:
            _total, count = struct.unpack_from("<IH", body, 20)
        except struct.error as exc:
            raise ProtocolDecodeError("invalid MAC quote header") from exc
        # The wire response carries a 20-byte field/control area.  The four
        # control bytes are not rows, but fields whose bits are defined in the
        # extended MAC vocabulary remain valid dynamic values in the response.
        # The MAC response carries the full 20-byte selector.  In addition to
        # the low 128 fields, newer servers put extended values in the high
        # control bytes; retaining all 20 bytes is what avoids easy_tdx's
        # bit>=128 overflow/truncation behavior.
        fields = active_mac_fields(bitmap)
        # The wire vocabulary reuses four bits for board statistics and
        # level-2/level-5 order-book quantities.  Preserve the board names for
        # 0x122C board requests and expose the stock-side aliases for 0x122B.
        stock_aliases = {
            MacField.LIMIT_UP_COUNT: "bid2_volume",
            MacField.LIMIT_DOWN_COUNT: "ask2_volume",
            MacField.UP_COUNT: "bid5_volume",
            MacField.DOWN_COUNT: "ask5_volume",
        }
        row_len = 68 + 4 * len(fields)
        rows: list[dict[str, object]] = []
        for index in range(min(count, (len(body) - 26) // row_len)):
            pos = 26 + index * row_len
            market = struct.unpack_from("<H", body, pos)[0]
            row: dict[str, object] = {"market": market, "code": _decode_text(body[pos + 2:pos + 24]), "name": _decode_text(body[pos + 24:pos + 68])}
            for field_index, field in enumerate(fields):
                raw = body[pos + 68 + field_index * 4:pos + 72 + field_index * 4]
                if len(raw) != 4:
                    break
                name = field.field_name if board else stock_aliases.get(field, field.field_name)
                row[name] = mac_field_value(raw, field)
            rows.append(row)
        return rows

    @staticmethod
    def _decode_board_list(body: bytes) -> list[dict[str, object]]:
        if len(body) < 4:
            return []
        count_all, _total = struct.unpack_from("<HH", body)
        row_fmt = struct.Struct("<H6s16s44sfffH6s16s44sfff")
        rows = []
        for i in range(min(count_all // 2, (len(body) - 4) // row_fmt.size)):
            values = row_fmt.unpack_from(body, 4 + i * row_fmt.size)
            rows.append({
                "market": values[0], "code": _decode_text(values[1]), "name": _decode_text(values[3]),
                "price": values[4], "rise_speed": values[5], "pre_close": values[6],
                "symbol_market": values[7], "symbol_code": _decode_text(values[8]),
                "symbol_name": _decode_text(values[10]), "symbol_price": values[11],
                "symbol_rise_speed": values[12], "symbol_pre_close": values[13],
            })
        return rows

    @staticmethod
    def _decode_belong(body: bytes) -> list[dict[str, object]]:
        rows = []
        for item in _json_body(body):
            if not isinstance(item, list) or len(item) not in (9, 13):
                continue
            rows.append({"board_type": int(float(item[0] or 0)), "market": int(float(item[1] or 0)), "board_code": str(item[2]), "board_name": str(item[3]), "close": float(item[4] or 0), "pre_close": float(item[5] or 0)})
        return rows

    @staticmethod
    def _decode_capital_flow(body: bytes) -> list[dict[str, object]]:
        values = _json_body(body)
        if len(values) < 2:
            return []
        today = values[0] if isinstance(values[0], list) else []
        days = values[1] if isinstance(values[1], list) else []
        main_in = float(today[0] or 0) if len(today) > 0 else 0.0
        main_out = float(today[1] or 0) if len(today) > 1 else 0.0
        retail_in = float(today[2] or 0) if len(today) > 2 else 0.0
        retail_out = float(today[3] or 0) if len(today) > 3 else 0.0
        buy_5d = float(days[0] or 0) if len(days) > 0 else 0.0
        sell_5d = float(days[1] or 0) if len(days) > 1 else 0.0
        super_large_5d = float(days[2] or 0) if len(days) > 2 else 0.0
        large_5d = float(days[3] or 0) if len(days) > 3 else 0.0
        mid_5d = float(days[4] or 0) if len(days) > 4 else 0.0
        small_5d = float(days[5] or 0) if len(days) > 5 else 0.0
        return [{
            "date": "",
            "main_in": main_in,
            "main_out": main_out,
            "main_net": main_in - main_out,
            "small_in": retail_in,
            "small_out": retail_out,
            "small_net": retail_in - retail_out,
            "mid_in": 0.0,
            "mid_out": 0.0,
            "mid_net": mid_5d,
            "large_in": 0.0,
            "large_out": 0.0,
            "large_net": large_5d,
            "buy_5d": buy_5d,
            "sell_5d": sell_5d,
            "super_large_net_5d": super_large_5d,
            "large_net_5d": large_5d,
            "mid_net_5d": mid_5d,
            "small_net_5d": small_5d,
        }]

    @staticmethod
    def _decode_symbol_info(body: bytes) -> list[dict[str, object]]:
        if len(body) < 180:
            return []
        market, code, name = struct.unpack_from("<H22s44s", body, 8)
        date_raw, time_raw, activity, pre_close, open_, high, low, close, momentum, vol, amount, inside, outside = struct.unpack_from("<III5ffIfII", body, 96)
        _decimal, _a, _b, _c, _vr, turnover, avg = struct.unpack_from("<HIf20xI3f", body, 148)
        try:
            stamp = datetime(  # noqa: DTZ001 - MAC timestamps are exchange-local and intentionally naive.
                date_raw // 10000,
                date_raw % 10000 // 100,
                date_raw % 100,
                time_raw // 10000,
                time_raw % 10000 // 100,
                time_raw % 100,
            )
        except ValueError:
            stamp = None
        return [{"market": market, "code": _decode_text(code), "name": _decode_text(name), "datetime": stamp, "activity": activity, "pre_close": pre_close, "open": open_, "high": high, "low": low, "close": close, "momentum": momentum, "vol": int(vol), "amount": amount, "inside_volume": inside, "outside_volume": outside, "turnover": turnover, "avg": avg}]

    @staticmethod
    def _decode_bars(body: bytes, frequency: int) -> list[dict[str, object]]:
        if len(body) < 33:
            return []
        _category, _flag, count, _start = struct.unpack_from("<HBHI", body, 24)
        count = min(count, max(0, (len(body) - 33) // 36))
        intraday = frequency < 4 or frequency in (7, 8)
        rows = []
        for i in range(count):
            ymd, time_num, open_, high, low, close, amount, vol, float_shares = struct.unpack_from("<II7f", body, 33 + i * 36)
            try:
                stamp = (
                    datetime(ymd // 10000, ymd % 10000 // 100, ymd % 100, time_num // 3600, time_num % 3600 // 60)  # noqa: DTZ001 - exchange-local naive timestamp
                    if intraday and time_num
                    else datetime(ymd // 10000, ymd % 10000 // 100, ymd % 100)  # noqa: DTZ001 - exchange-local naive timestamp
                )
            except ValueError:
                continue
            rows.append({"datetime": stamp, "open": open_, "high": high, "low": low, "close": close, "vol": vol, "amount": amount, "float_shares": float_shares})
        return rows

    @staticmethod
    def _decode_tick_chart(body: bytes) -> list[dict[str, object]]:
        if len(body) < 35:
            return []
        count = struct.unpack_from("<H", body, 33)[0]
        rows = []
        for i in range(min(count, (len(body) - 35) // 18)):
            minutes, price, avg, vol, momentum = struct.unpack_from("<HffIf", body, 35 + i * 18)
            rows.append({"time": time(minutes // 60 % 24, minutes % 60), "price": price, "avg": avg, "vol": vol, "momentum": momentum})
        return rows

    @staticmethod
    def _decode_tick_charts(body: bytes) -> list[dict[str, object]]:
        if len(body) < 71:
            return []
        dates = struct.unpack_from("<5I", body, 24)
        count, _last, page_size, total = struct.unpack_from("<HBHH", body, 64)
        size = struct.calcsize("<HffHH")
        # The response ends with a fixed 104-byte snapshot tail. Keep it out
        # of the tick budget so a malformed total cannot consume metadata as
        # an extra trade.
        tail_size = struct.calcsize("<44sBHf5x2I5ffIf12s2fI")
        max_ticks = max(0, (len(body) - 71 - tail_size) // size)
        total = min(total, max_ticks)
        days = []
        if not count or not page_size:
            tick_counts: list[int] = []
        elif total >= count * page_size:
            tick_counts = [page_size] * count
        else:
            # With date=0 the server sends the latest partial day first,
            # followed by complete historical days.  Assigning page_size to
            # the first day (the old implementation) shifts all ticks into
            # the wrong dates whenever the market is still open.
            complete_days = min(count - 1, total // page_size)
            first_day_count = total - complete_days * page_size
            tick_counts = [first_day_count] + [page_size] * complete_days
            tick_counts.extend([0] * (count - len(tick_counts)))
        pos = 71
        for day_index, current in enumerate(tick_counts[:5]):
            for tick_index in range(current):
                minutes, price, avg, vol, _reserved = struct.unpack_from("<HffHH", body, pos)
                pos += size
                days.append({"date": str(dates[day_index]), "time": time(minutes // 60 % 24, minutes % 60), "price": price, "avg": avg, "vol": vol})
        return days

    @staticmethod
    def _decode_sampling(body: bytes) -> list[dict[str, object]]:
        if len(body) < 42:
            return []
        count = struct.unpack_from("<H", body, 40)[0]
        return [{"price": struct.unpack_from("<f", body, 42 + i * 4)[0]} for i in range(min(count, (len(body) - 42) // 4))]

    @staticmethod
    def _decode_transactions(body: bytes) -> list[dict[str, object]]:
        if len(body) < 31:
            return []
        count = struct.unpack_from("<H", body, 29)[0]
        rows = []
        for i in range(min(count, (len(body) - 39) // 18)):
            seconds, price, volume, trade_count, side = struct.unpack_from("<IfIIH", body, 39 + i * 18)
            rows.append({"time": time(seconds // 3600 % 24, seconds % 3600 // 60, seconds % 60), "price": price, "vol": volume, "trade_count": trade_count, "bs_flag": side})
        return rows

    @staticmethod
    def _decode_auction(body: bytes) -> list[dict[str, object]]:
        if len(body) < 36:
            return []
        count = struct.unpack_from("<I", body, 24)[0]
        rows = []
        for i in range(min(count, (len(body) - 36) // 16)):
            seconds, price, matched, unmatched = struct.unpack_from("<IfIi", body, 36 + i * 16)
            rows.append({"time": time(seconds // 3600 % 24, seconds % 3600 // 60, seconds % 60), "price": price, "matched": matched, "unmatched": unmatched})
        return rows

    @staticmethod
    def _decode_unusual(body: bytes) -> list[dict[str, object]]:
        if len(body) < 2:
            return []
        count = struct.unpack_from("<H", body)[0]
        rows = []
        for i in range(min(count, (len(body) - 2) // 32)):
            pos = 2 + i * 32
            market, code, _pad1, unusual_type, _pad2, index, _z = struct.unpack_from("<H6sBBBHH", body, pos)
            hour, minute_second = struct.unpack_from("<BH", body, pos + 29)
            description, value = _describe_unusual(unusual_type, body[pos + 15 : pos + 28])
            rows.append({
                "index": index,
                "market": market,
                "code": _decode_text(code),
                "name": "",
                "time": time(hour, minute_second // 100, minute_second % 100),
                "unusual_type": unusual_type,
                "description": description,
                "value": value,
                "raw": body[pos:pos + 32],
            })
        text_values = body[2 + count * 32 :].decode("gbk", errors="replace").strip(",").split(",")
        for index, row in enumerate(rows):
            if index < len(text_values):
                row["name"] = text_values[index]
        return rows

    @staticmethod
    def _decode_server_info(body: bytes) -> list[dict[str, object]]:
        if len(body) < 87:
            return []
        # count/flags/tag/reserved = 22 bytes, then today date and timestamp.
        pos = 22
        today_raw = struct.unpack_from("<I", body, pos)[0]
        pos += 8
        sessions = []
        for _ in range(2):
            values = struct.unpack_from("<8H", body, pos)
            pos += 16
            sessions.append([
                {
                    "open": f"{values[i] // 60}:{values[i] % 60:02d}",
                    "close": f"{values[i + 1] // 60}:{values[i + 1] % 60:02d}",
                }
                for i in range(0, 8, 2)
            ])
        pos += 1
        last_raw = struct.unpack_from("<I", body, pos)[0]
        pos += 8
        market_param_1 = struct.unpack_from("<I", body, pos)[0] if pos + 4 <= len(body) else 0
        pos += 4
        market_param_2 = struct.unpack_from("<I", body, pos)[0] if pos + 4 <= len(body) else 0
        return [{
            "today": f"{today_raw // 10000:04d}-{today_raw % 10000 // 100:02d}-{today_raw % 100:02d}",
            "last_trading_day": f"{last_raw // 10000:04d}-{last_raw % 10000 // 100:02d}-{last_raw % 100:02d}",
            "sessions_1": sessions[0],
            "sessions_2": sessions[1],
            "market_param_1": market_param_1,
            "market_param_2": market_param_2,
        }]

    @staticmethod
    def _decode_kline_offset(body: bytes) -> list[dict[str, object]]:
        if len(body) < 8:
            return []
        return [{"total": struct.unpack_from(">I", body)[0], "returned": struct.unpack_from("<I", body, 4)[0]}]

    @staticmethod
    def _decode_file_meta(body: bytes) -> dict[str, object]:
        if len(body) < 41:
            return {"offset": 0, "size": 0, "flag": 0, "hash": ""}
        offset, size, flag = struct.unpack_from("<IIb", body)
        return {"offset": offset, "size": size, "flag": flag, "hash": _decode_text(body[9:41])}

    @staticmethod
    def _decode_goods(body: bytes) -> list[dict[str, object]]:
        if len(body) < 2:
            return []
        total = struct.unpack_from("<H", body)[0]
        fmt = struct.Struct("<H23sHIBfffHH")
        rows = []
        for i in range(min(total, (len(body) - 2) // fmt.size)):
            category, name, u, index, switch, v1, v2, v3, c1, c2 = fmt.unpack_from(body, 2 + i * fmt.size)
            rows.append({"category": category, "name": _decode_text(name), "u": u, "index": index, "switch": switch, "code": [v1, v2, v3], "c1": c1, "c2": c2})
        return rows


class MacExProtocol(MacProtocol):
    """7727 MAC EX 协议；消息格式相同，但请求头使用 0x01。"""

    head_flag = 0x01


__all__ = ["MacExProtocol", "MacProtocol", "build_mac_request"]
