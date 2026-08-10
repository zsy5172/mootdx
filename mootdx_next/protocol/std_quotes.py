from __future__ import annotations

import math
import struct
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from mootdx_next.constants import MARKET_BJ
from mootdx_next.constants import MAX_LIMIT_PRICE_COUNT
from mootdx_next.errors import ProtocolDecodeError
from mootdx_next.errors import UnsupportedMarketError
from mootdx_next.interfaces import AbstractProtocol
from mootdx_next.models import ResponseEnvelope
from mootdx_next.symbols import get_security_coefficient
from mootdx_next.symbols import get_security_type

XDXR_CATEGORY_MAPPING = {
    1: "除权除息",
    2: "送配股上市",
    3: "非流通股上市",
    4: "未知股本变动",
    5: "股本变化",
    6: "增发新股",
    7: "股份回购",
    8: "增发新股上市",
    9: "转配股上市",
    10: "可转债上市",
    11: "扩缩股",
    12: "非流通股缩股",
    13: "送认购权证",
    14: "送认沽权证",
}

TRADING_PHASES = {
    0: "",
    1: "开盘前",
    2: "开盘集合竞价",
    3: "连续竞价",
    4: "收盘集合竞价",
    5: "闭市阶段",
    6: "连续竞价闭市",
    7: "盘后连续撮合",
    8: "停牌",
    9: "",
    10: "",
    11: "盘后停牌",
    12: "波动中断",
    13: "盘中休市",
    14: "匹配临时停牌",
}

U16_STRUCT = struct.Struct("<H")
U32_STRUCT = struct.Struct("<I")
U16_PAIR_STRUCT = struct.Struct("<HH")
STOCK_LIST_ROW_STRUCT = struct.Struct("<6sH8s4sBI4s")
QUOTE_HEAD_STRUCT = struct.Struct("<B6sH")
QUOTE_TAIL_STRUCT = struct.Struct("<hH")
FINANCE_HEAD_STRUCT = struct.Struct("<B6s")
FINANCE_BODY_STRUCT = struct.Struct("<fHHIIffffffffffffffffffffffffffffff")
XDXR_FLOAT4_STRUCT = struct.Struct("<ffff")
XDXR_MIXED_STRUCT = struct.Struct("<IIfI")
XDXR_WARRANT_STRUCT = struct.Struct("<fIfI")
XDXR_UINT4_STRUCT = struct.Struct("<IIII")
F10_CATEGORY_STRUCT = struct.Struct("<64s80sII")
F10_CONTENT_HEAD_STRUCT = struct.Struct("<10sH")
BLOCK_INFO_META_STRUCT = struct.Struct("<I1s32s1s")
ZIP_DAY_MINUTES_STRUCT = struct.Struct("<HH")
QUOTE_TRADING_PHASE_STRUCT = struct.Struct("<H")
FUND_FLOW_FIXED_STRUCT = struct.Struct("<hhfHH10fH")
FUND_FLOW_EXTENSION_STRUCT = struct.Struct("<fff48H")
LIMIT_PRICE_REQUEST_STRUCT = struct.Struct("<HHHHHHHH")
LIMIT_PRICE_ROW_STRUCT = struct.Struct("<BIff")
CALL_AUCTION_ROW_STRUCT = struct.Struct("<HfIiBB")


def _get_volume(vol: int) -> float:
    # The wire value zero is an exact zero.  The logarithmic decoder below
    # otherwise treats it like a denormalized floating-point value and emits
    # 2**-127, which turns empty bars into tiny non-zero volume/amount rows.
    if vol == 0:
        return 0.0

    logpoint = vol >> (8 * 3)

    hleax = (vol >> (8 * 2)) & 0xFF
    lheax = (vol >> 8) & 0xFF
    lleax = vol & 0xFF

    dw_ecx = logpoint * 2 - 0x7F
    dw_edx = logpoint * 2 - 0x86

    dw_esi = logpoint * 2 - 0x8E
    dw_eax = logpoint * 2 - 0x96

    tmp_eax = -dw_ecx if dw_ecx < 0 else dw_ecx
    dbl_xmm6 = pow(2.0, tmp_eax)

    if dw_ecx < 0:
        dbl_xmm6 = 1.0 / dbl_xmm6

    if hleax > 0x80:
        dwtmpeax = dw_edx + 1
        tmpdbl_xmm3 = pow(2.0, dwtmpeax)

        dbl_xmm0 = pow(2.0, dw_edx) * 128.0
        dbl_xmm0 += (hleax & 0x7F) * tmpdbl_xmm3
        dbl_xmm4 = dbl_xmm0
    else:
        if dw_edx >= 0:
            dbl_xmm0 = pow(2.0, dw_edx) * hleax
        else:
            dbl_xmm0 = (1 / pow(2.0, dw_edx)) * hleax
        dbl_xmm4 = dbl_xmm0

    dbl_xmm3 = pow(2.0, dw_esi) * lheax
    dbl_xmm1 = pow(2.0, dw_eax) * lleax

    if hleax & 0x80:
        dbl_xmm3 *= 2.0
        dbl_xmm1 *= 2.0

    return dbl_xmm6 + dbl_xmm4 + dbl_xmm3 + dbl_xmm1


def _index_bytes(data: bytes, pos: int) -> int:
    return data[pos]


def _get_price(data: bytes, pos: int) -> tuple[int, int]:
    pos_byte = 6
    bdata = _index_bytes(data, pos)
    int_data = bdata & 0x3F
    sign = bool(bdata & 0x40)

    if bdata & 0x80:
        while True:
            pos += 1
            bdata = _index_bytes(data, pos)
            int_data += (bdata & 0x7F) << pos_byte
            pos_byte += 7
            if not (bdata & 0x80):
                break

    pos += 1
    if sign:
        int_data = -int_data

    return int_data, pos


def _get_security_type(market: int, code: str) -> str:
    return get_security_type(market, code)


def _get_security_coefficient(market: int, code: str) -> float:
    return get_security_coefficient(market, code)


def _format_quotes_time(time_stamp: int) -> str | int:
    if not int(time_stamp):
        return time_stamp

    time_string = str(time_stamp)
    formatted = time_string[:8][:-6] + ":"
    if int(time_string[-6:-4]) < 60:
        formatted += f"{time_string[-6:-4]}:"
        formatted += "%06.3f" % (int(time_string[-4:]) * 60 / 10000.0)
    else:
        formatted += "%02d:" % (int(time_string[-6:]) * 60 / 1000000)
        formatted += "%06.3f" % ((int(time_string[-6:]) * 60 % 1000000) * 60 / 1000000.0)

    return formatted


def _cal_price(base_price: int, diff: int, coefficient: float = 0.01) -> float:
    return float(base_price + diff) * coefficient


def _scale_integer_price(value: int, coefficient: float) -> float:
    """Scale a protocol integer without multiplication-induced float tails."""

    divisor = round(1.0 / coefficient)
    return float(value) / divisor


def _decode_gbk_string(value: bytes) -> str:
    zero = value.find(b"\x00")
    if zero != -1:
        value = value[:zero]
    return value.decode("gbk", "ignore")


def _format_yyyymmdd(value: str | int) -> str:
    raw = str(value).strip().replace("-", "")
    try:
        parsed = datetime.strptime(raw, "%Y%m%d")
    except ValueError as exc:
        raise ProtocolDecodeError(f"invalid response context date: {value}") from exc
    return parsed.strftime("%Y-%m-%d")


def _minute_slot(index: int) -> tuple[int, int]:
    if not 0 <= index < 240:
        raise ProtocolDecodeError(f"standard minute row index out of range: {index}")
    absolute_minute = 9 * 60 + 31 + index if index < 120 else 13 * 60 + 1 + index - 120
    return divmod(absolute_minute, 60)


def _transaction_side_name(value: int) -> str:
    if value == 0:
        return "buy"
    if value == 1:
        return "sell"
    return "neutral"


def _market_prefix(market: int) -> str:
    return {0: "sz", 1: "sh", 2: "bj"}.get(int(market), f"m{market}")


class StdQuoteProtocol(AbstractProtocol):
    valid_markets = {0, 1}
    security_markets = {0, 1, MARKET_BJ}
    stock_count_markets = {0, 1, 2}
    quote_markets = {0, 1, 2}

    def encode(self, api: str, **kwargs: Any) -> bytes:
        if api == "stock_count":
            return self.encode_stock_count(int(kwargs["market"]))
        if api == "stock_list_page":
            return self.encode_stock_list_page(int(kwargs["market"]), int(kwargs["start"]))
        if api == "quotes":
            return self.encode_quotes(list(kwargs["symbols"]))
        if api == "fund_flows":
            return self.encode_fund_flows(list(kwargs["symbols"]))
        if api == "limit_prices":
            return self.encode_limit_prices(int(kwargs["start"]), int(kwargs["count"]))
        if api == "call_auction":
            return self.encode_call_auction(int(kwargs["market"]), str(kwargs["code"]))
        if api == "bars":
            return self.encode_bars(
                int(kwargs["frequency"]),
                int(kwargs["market"]),
                str(kwargs["code"]),
                int(kwargs["start"]),
                int(kwargs["count"]),
            )
        if api == "index_bars":
            return self.encode_index_bars(
                int(kwargs["frequency"]),
                int(kwargs["market"]),
                str(kwargs["code"]),
                int(kwargs["start"]),
                int(kwargs["count"]),
            )
        if api == "minutes":
            return self.encode_minutes(int(kwargs["market"]), str(kwargs["code"]), kwargs["date"])
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
                int(kwargs["start"]),
                int(kwargs["count"]),
                kwargs["date"],
            )
        if api == "finance":
            return self.encode_finance(int(kwargs["market"]), str(kwargs["code"]))
        if api == "xdxr":
            return self.encode_xdxr(int(kwargs["market"]), str(kwargs["code"]))
        if api == "f10_categories":
            return self.encode_f10_categories(int(kwargs["market"]), str(kwargs["code"]))
        if api == "f10_content":
            return self.encode_f10_content(
                int(kwargs["market"]),
                str(kwargs["code"]),
                kwargs["filename"],
                int(kwargs["start"]),
                int(kwargs["length"]),
            )
        if api == "block_info_meta":
            return self.encode_block_info_meta(str(kwargs["block_file"]))
        if api == "block_info":
            return self.encode_block_info(str(kwargs["block_file"]), int(kwargs["start"]), int(kwargs["size"]))
        raise NotImplementedError(f"unsupported protocol api: {api}")

    def decode(self, api: str, envelope: ResponseEnvelope, **kwargs: Any) -> object:
        body = envelope.body or b""
        if api == "stock_count":
            return self.decode_stock_count(body)
        if api == "stock_list_page":
            return self.decode_stock_list_page(body)
        if api == "quotes":
            return self.decode_quotes(body, price_coefficients=kwargs.get("price_coefficients"))
        if api == "fund_flows":
            return self.decode_fund_flows(body, price_coefficients=kwargs.get("price_coefficients"))
        if api == "limit_prices":
            return self.decode_limit_prices(body)
        if api == "call_auction":
            return self.decode_call_auction(body)
        if api == "bars":
            return self.decode_bars(body, int(kwargs["frequency"]))
        if api == "index_bars":
            return self.decode_index_bars(body, int(kwargs["frequency"]))
        if api == "minutes":
            return self.decode_minutes(
                body,
                int(kwargs["market"]),
                str(kwargs["code"]),
                date=kwargs["date"],
                price_coefficient=kwargs.get("price_coefficient"),
            )
        if api == "transaction":
            return self.decode_transaction(
                body,
                market=int(kwargs["market"]),
                code=str(kwargs["code"]),
                price_coefficient=kwargs.get("price_coefficient"),
            )
        if api == "transactions":
            return self.decode_history_transactions(
                body,
                market=int(kwargs["market"]),
                code=str(kwargs["code"]),
                date=kwargs["date"],
                price_coefficient=kwargs.get("price_coefficient"),
            )
        if api == "finance":
            return self.decode_finance(body)
        if api == "xdxr":
            return self.decode_xdxr(body)
        if api == "f10_categories":
            return self.decode_f10_categories(body)
        if api == "f10_content":
            if bool(kwargs.get("raw")):
                return self.decode_f10_content_bytes(body)
            return self.decode_f10_content(body)
        if api == "block_info_meta":
            return self.decode_block_info_meta(body)
        if api == "block_info":
            return self.decode_block_info(body)
        raise NotImplementedError(f"unsupported protocol api: {api}")

    def encode_stock_count(self, market: int) -> bytes:
        if market not in self.stock_count_markets:
            raise UnsupportedMarketError(f"unsupported market for stock_count: {market}")

        payload = bytearray.fromhex("0c 0c 18 6c 00 01 08 00 08 00 4e 04")
        payload.extend(struct.pack("<H", market))
        payload.extend(b"\x75\xc7\x33\x01")
        return bytes(payload)

    def decode_stock_count(self, body: bytes) -> int:
        if len(body) < 2:
            raise ProtocolDecodeError(f"stock_count body too short: {len(body)}")

        try:
            (count,) = U16_STRUCT.unpack_from(body, 0)
        except struct.error as exc:
            raise ProtocolDecodeError("failed to decode stock_count body") from exc

        return count

    def encode_stock_list_page(self, market: int, start: int) -> bytes:
        if market not in self.valid_markets:
            raise UnsupportedMarketError(f"unsupported market for stocks: {market}")
        if start < 0:
            raise ProtocolDecodeError(f"invalid stock list start: {start}")

        payload = bytearray.fromhex("0c 01 18 64 01 01 06 00 06 00 50 04")
        payload.extend(struct.pack("<HH", market, start))
        return bytes(payload)

    def decode_stock_list_page(self, body: bytes) -> list[dict[str, object]]:
        if len(body) < 2:
            raise ProtocolDecodeError(f"stock_list_page body too short: {len(body)}")

        try:
            (num_rows,) = U16_STRUCT.unpack_from(body, 0)
        except struct.error as exc:
            raise ProtocolDecodeError("failed to decode stock_list_page count") from exc

        pos = 2
        rows: list[dict[str, object]] = []

        for index in range(num_rows):
            chunk = body[pos : pos + 29]
            if len(chunk) != 29:
                raise ProtocolDecodeError(
                    f"stock_list_page row {index} truncated: expected 29 bytes, got {len(chunk)}"
                )

            try:
                code, volunit, name_bytes, _reserved_1, decimal_point, pre_close_raw, _reserved_2 = (
                    STOCK_LIST_ROW_STRUCT.unpack_from(body, pos)
                )
            except struct.error as exc:
                raise ProtocolDecodeError(f"failed to decode stock_list_page row {index}") from exc

            rows.append(
                {
                    "code": code.decode("utf-8", errors="ignore"),
                    "volunit": volunit,
                    "decimal_point": decimal_point,
                    "name": _decode_gbk_string(name_bytes),
                    "pre_close": _get_volume(pre_close_raw),
                }
            )
            pos += 29

        return rows

    def decode_stock_list_pages(self, pages: list[bytes]) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for body in pages:
            rows.extend(self.decode_stock_list_page(body))
        return rows

    def encode_quotes(self, symbols: list[tuple[int, str]]) -> bytes:
        if not symbols:
            raise ProtocolDecodeError("quotes request requires at least one symbol")

        payload_len = len(symbols) * 7 + 12
        values = (0x10C, 0x02006320, payload_len, payload_len, 0x5053E, 0, 0, len(symbols))
        payload = bytearray(struct.pack("<HIHHIIHH", *values))

        for market, code in symbols:
            if market not in self.quote_markets:
                raise UnsupportedMarketError(f"unsupported market for quotes: {market}")
            encoded_code = code.encode("utf-8")
            payload.extend(struct.pack("<B6s", market, encoded_code))

        return bytes(payload)

    def encode_fund_flows(self, symbols: list[tuple[int, str]]) -> bytes:
        """Encode the mode-1 quote request used by the fund-flow pages."""

        if not symbols:
            raise ProtocolDecodeError("fund_flows request requires at least one symbol")
        if len(symbols) > 80:
            raise ProtocolDecodeError("fund_flows request supports at most 80 symbols")

        payload_len = len(symbols) * 7 + 12
        values = (0x10C, 0x02006320, payload_len, payload_len, 0x5054C, 0x100, 0, len(symbols))
        payload = bytearray(struct.pack("<HIHHIIHH", *values))

        for market, code in symbols:
            if market not in self.quote_markets:
                raise UnsupportedMarketError(f"unsupported market for fund_flows: {market}")
            encoded_code = code.encode("ascii")
            if len(encoded_code) != 6 or not encoded_code.isdigit():
                raise ProtocolDecodeError("fund_flows symbols must contain six-digit numeric codes")
            payload.extend(struct.pack("<B6s", market, encoded_code))

        return bytes(payload)

    def decode_fund_flows(
        self,
        body: bytes,
        *,
        price_coefficients: Mapping[tuple[int, str], float] | None = None,
    ) -> list[dict[str, object]]:
        """Decode a 0x054C mode-1 quote and its fund-flow extension."""

        if len(body) < 4:
            raise ProtocolDecodeError(f"fund_flows body too short: {len(body)}")

        mode, num_rows = U16_PAIR_STRUCT.unpack_from(body, 0)
        if mode != 1:
            raise ProtocolDecodeError(f"fund_flows response has unexpected mode: {mode}")

        pos = 4
        rows: list[dict[str, object]] = []
        try:
            for _index in range(num_rows):
                market, code_bytes, active = QUOTE_HEAD_STRUCT.unpack_from(body, pos)
                pos += QUOTE_HEAD_STRUCT.size
                code = code_bytes.decode("ascii")

                price, pos = _get_price(body, pos)
                last_close_diff, pos = _get_price(body, pos)
                open_diff, pos = _get_price(body, pos)
                high_diff, pos = _get_price(body, pos)
                low_diff, pos = _get_price(body, pos)
                server_time_raw, pos = _get_price(body, pos)
                reversed_bytes1, pos = _get_price(body, pos)
                volume, pos = _get_price(body, pos)
                current_volume, pos = _get_price(body, pos)

                # The block-index parser reads this as IEEE-754 float. It is
                # also the exact denominator for the amount-share columns.
                (amount,) = struct.unpack_from("<f", body, pos)
                pos += 4

                sell_volume, pos = _get_price(body, pos)
                buy_volume, pos = _get_price(body, pos)
                reversed_bytes2, pos = _get_price(body, pos)
                reversed_bytes3, pos = _get_price(body, pos)
                bid1_diff, pos = _get_price(body, pos)
                ask1_diff, pos = _get_price(body, pos)
                bid_volume1, pos = _get_price(body, pos)
                ask_volume1, pos = _get_price(body, pos)
                (trading_status_word,) = QUOTE_TRADING_PHASE_STRUCT.unpack_from(body, pos)
                pos += QUOTE_TRADING_PHASE_STRUCT.size

                fixed = FUND_FLOW_FIXED_STRUCT.unpack_from(body, pos)
                pos += FUND_FLOW_FIXED_STRUCT.size
                extension = FUND_FLOW_EXTENSION_STRUCT.unpack_from(body, pos)
                pos += FUND_FLOW_EXTENSION_STRUCT.size

                coefficient = price_coefficients.get((market, code)) if price_coefficients is not None else None
                if coefficient is None:
                    coefficient = _get_security_coefficient(market, code)

                short_momentum_raw, short_turnover_raw, amount_2min = fixed[:3]
                fixed_unknown_1, fixed_unknown_2 = fixed[3:5]
                fixed_floats = fixed[5:15]
                fixed_tail = fixed[15]
                main_buy_amount = float(fixed_floats[0])
                main_net_amount = float(fixed_floats[1])
                volume_growth_rate = float(fixed_floats[2])

                fund_amount_base, fund_volume_base, retail_order_base = extension[:3]
                words = tuple(int(value) for value in extension[3:])
                amount_scale = float(fund_amount_base) / 50_000.0

                daily_super_large = (words[0] - words[1]) * amount_scale
                daily_large = (words[4] - words[5]) * amount_scale
                daily_medium = (words[8] - words[9]) * amount_scale
                daily_small = (words[12] - words[13]) * amount_scale

                five_minute_super_large = (words[40] - words[41]) * amount_scale
                five_minute_large = (words[42] - words[43]) * amount_scale
                five_minute_medium = (words[44] - words[45]) * amount_scale
                five_minute_small = (words[46] - words[47]) * amount_scale
                five_minute_main = five_minute_super_large + five_minute_large

                retail_scale = float(retail_order_base) / 50_000.0
                retail_order_growth_ratio = (
                    (words[35] + words[33] - words[34] - words[32]) * retail_scale / 100.0
                )

                rows.append(
                    {
                        "market": market,
                        "code": code,
                        "active": active,
                        "price": _cal_price(price, 0, coefficient),
                        "last_close": _cal_price(price, last_close_diff, coefficient),
                        "open": _cal_price(price, open_diff, coefficient),
                        "high": _cal_price(price, high_diff, coefficient),
                        "low": _cal_price(price, low_diff, coefficient),
                        "servertime": _format_quotes_time(server_time_raw),
                        "volume": volume,
                        "current_volume": current_volume,
                        "amount": float(amount),
                        "sell_volume": sell_volume,
                        "buy_volume": buy_volume,
                        "bid1": _cal_price(price, bid1_diff, coefficient),
                        "ask1": _cal_price(price, ask1_diff, coefficient),
                        "bid_volume1": bid_volume1,
                        "ask_volume1": ask_volume1,
                        "trading_phase": (trading_status_word >> 2) & 0x0F,
                        "short_momentum_rate": short_momentum_raw / 100.0,
                        "short_turnover_rate": short_turnover_raw / 100.0,
                        "amount_2min": float(amount_2min),
                        "volume_growth_rate": volume_growth_rate,
                        "main_buy_amount": main_buy_amount,
                        "main_net_amount": main_net_amount,
                        "main_buy_share": main_buy_amount * 100.0 / amount if amount else 0.0,
                        "main_force_share": main_net_amount * 100.0 / amount if amount else 0.0,
                        "super_large_net_amount": daily_super_large,
                        "large_net_amount": daily_large,
                        "medium_net_amount": daily_medium,
                        "small_net_amount": daily_small,
                        "main_net_amount_5min": five_minute_main,
                        "main_force_share_5min": five_minute_main * 100.0 / amount if amount else 0.0,
                        "super_large_net_amount_5min": five_minute_super_large,
                        "large_net_amount_5min": five_minute_large,
                        "medium_net_amount_5min": five_minute_medium,
                        "small_net_amount_5min": five_minute_small,
                        "retail_order_growth_ratio": retail_order_growth_ratio,
                        "reversed_bytes1": reversed_bytes1,
                        "reversed_bytes2": reversed_bytes2,
                        "reversed_bytes3": reversed_bytes3,
                        "fund_amount_base": float(fund_amount_base),
                        "fund_volume_base": float(fund_volume_base),
                        "retail_order_base": float(retail_order_base),
                        "raw_fixed_fields": (
                            fixed_unknown_1,
                            fixed_unknown_2,
                            *tuple(float(value) for value in fixed_floats[3:]),
                            fixed_tail,
                        ),
                        "raw_fund_words": words,
                    }
                )
        except (IndexError, UnicodeDecodeError, struct.error) as exc:
            raise ProtocolDecodeError(f"failed to decode fund_flows row {len(rows)}") from exc

        if pos != len(body):
            raise ProtocolDecodeError(f"fund_flows response has {len(body) - pos} unconsumed bytes")
        return rows

    def encode_limit_prices(self, start: int = 0, count: int = MAX_LIMIT_PRICE_COUNT) -> bytes:
        if start < 0 or start > 0xFFFF:
            raise ValueError("start must be between 0 and 65535")
        if count <= 0 or count > MAX_LIMIT_PRICE_COUNT:
            raise ValueError(f"count must be between 1 and {MAX_LIMIT_PRICE_COUNT}")

        body = LIMIT_PRICE_REQUEST_STRUCT.pack(0x0452, start, 0, count, 0, 0, 0, 0)
        return struct.pack("<HIHH", 0, 0, len(body), len(body)) + body

    def decode_limit_prices(self, body: bytes) -> list[dict[str, object]]:
        if len(body) < U16_STRUCT.size:
            raise ProtocolDecodeError(f"limit_prices body too short: {len(body)}")

        try:
            (count,) = U16_STRUCT.unpack_from(body, 0)
        except struct.error as exc:
            raise ProtocolDecodeError("failed to decode limit_prices count") from exc

        expected_size = U16_STRUCT.size + count * LIMIT_PRICE_ROW_STRUCT.size
        if len(body) != expected_size:
            raise ProtocolDecodeError(
                f"invalid limit_prices body size: expected {expected_size} bytes, got {len(body)}"
            )

        rows: list[dict[str, object]] = []
        for index in range(count):
            pos = U16_STRUCT.size + index * LIMIT_PRICE_ROW_STRUCT.size
            try:
                market, numeric_code, limit_up, limit_down = LIMIT_PRICE_ROW_STRUCT.unpack_from(body, pos)
            except struct.error as exc:
                raise ProtocolDecodeError(f"failed to decode limit_prices row {index}") from exc

            if not math.isfinite(limit_up) or not math.isfinite(limit_down):
                raise ProtocolDecodeError(f"limit_prices row {index} contains a non-finite price")

            rows.append(
                {
                    "market": market,
                    "code": f"{numeric_code:06d}",
                    "limit_up": round(limit_up, 4),
                    "limit_down": round(limit_down, 4),
                }
            )

        return rows

    def decode_quotes(
        self,
        body: bytes,
        *,
        price_coefficients: Mapping[tuple[int, str], float] | None = None,
    ) -> list[dict[str, object]]:
        if len(body) < 4:
            raise ProtocolDecodeError(f"quotes body too short: {len(body)}")

        pos = 2
        try:
            (num_stock,) = U16_STRUCT.unpack_from(body, pos)
        except struct.error as exc:
            raise ProtocolDecodeError("failed to decode quotes count") from exc

        pos += 2
        rows: list[dict[str, object]] = []

        try:
            for index in range(num_stock):
                market, code, active1 = QUOTE_HEAD_STRUCT.unpack_from(body, pos)
                pos += 9

                price, pos = _get_price(body, pos)
                last_close_diff, pos = _get_price(body, pos)
                open_diff, pos = _get_price(body, pos)
                high_diff, pos = _get_price(body, pos)
                low_diff, pos = _get_price(body, pos)
                reversed_bytes0, pos = _get_price(body, pos)
                reversed_bytes1, pos = _get_price(body, pos)
                vol, pos = _get_price(body, pos)
                cur_vol, pos = _get_price(body, pos)

                (amount_raw,) = U32_STRUCT.unpack_from(body, pos)
                amount = _get_volume(amount_raw)
                pos += 4

                s_vol, pos = _get_price(body, pos)
                b_vol, pos = _get_price(body, pos)
                reversed_bytes2, pos = _get_price(body, pos)
                reversed_bytes3, pos = _get_price(body, pos)
                bid1, pos = _get_price(body, pos)
                ask1, pos = _get_price(body, pos)
                bid_vol1, pos = _get_price(body, pos)
                ask_vol1, pos = _get_price(body, pos)
                bid2, pos = _get_price(body, pos)
                ask2, pos = _get_price(body, pos)
                bid_vol2, pos = _get_price(body, pos)
                ask_vol2, pos = _get_price(body, pos)
                bid3, pos = _get_price(body, pos)
                ask3, pos = _get_price(body, pos)
                bid_vol3, pos = _get_price(body, pos)
                ask_vol3, pos = _get_price(body, pos)
                bid4, pos = _get_price(body, pos)
                ask4, pos = _get_price(body, pos)
                bid_vol4, pos = _get_price(body, pos)
                ask_vol4, pos = _get_price(body, pos)
                bid5, pos = _get_price(body, pos)
                ask5, pos = _get_price(body, pos)
                bid_vol5, pos = _get_price(body, pos)
                ask_vol5, pos = _get_price(body, pos)
                (trading_status_word,) = QUOTE_TRADING_PHASE_STRUCT.unpack_from(body, pos)
                trading_phase = (trading_status_word >> 2) & 0x0F
                pos += 2
                reversed_bytes5, pos = _get_price(body, pos)
                reversed_bytes6, pos = _get_price(body, pos)
                reversed_bytes7, pos = _get_price(body, pos)
                reversed_bytes8, pos = _get_price(body, pos)
                rate_raw, active2 = QUOTE_TAIL_STRUCT.unpack_from(body, pos)
                pos += 4

                decoded_code = code.decode("utf-8")
                coefficient = (
                    price_coefficients.get((market, decoded_code))
                    if price_coefficients is not None
                    else None
                )
                if coefficient is None:
                    coefficient = _get_security_coefficient(market, decoded_code)
                rows.append(
                    {
                        "market": market,
                        "code": decoded_code,
                        "active1": active1,
                        "price": _cal_price(price, 0, coefficient),
                        "last_close": _cal_price(price, last_close_diff, coefficient),
                        "open": _cal_price(price, open_diff, coefficient),
                        "high": _cal_price(price, high_diff, coefficient),
                        "low": _cal_price(price, low_diff, coefficient),
                        "servertime": _format_quotes_time(reversed_bytes0),
                        "reversed_bytes0": reversed_bytes0,
                        "reversed_bytes1": reversed_bytes1,
                        "vol": vol,
                        "cur_vol": cur_vol,
                        "amount": amount,
                        "s_vol": s_vol,
                        "b_vol": b_vol,
                        "reversed_bytes2": reversed_bytes2,
                        "reversed_bytes3": reversed_bytes3,
                        "bid1": _cal_price(price, bid1, coefficient),
                        "ask1": _cal_price(price, ask1, coefficient),
                        "bid_vol1": bid_vol1,
                        "ask_vol1": ask_vol1,
                        "bid2": _cal_price(price, bid2, coefficient),
                        "ask2": _cal_price(price, ask2, coefficient),
                        "bid_vol2": bid_vol2,
                        "ask_vol2": ask_vol2,
                        "bid3": _cal_price(price, bid3, coefficient),
                        "ask3": _cal_price(price, ask3, coefficient),
                        "bid_vol3": bid_vol3,
                        "ask_vol3": ask_vol3,
                        "bid4": _cal_price(price, bid4, coefficient),
                        "ask4": _cal_price(price, ask4, coefficient),
                        "bid_vol4": bid_vol4,
                        "ask_vol4": ask_vol4,
                        "bid5": _cal_price(price, bid5, coefficient),
                        "ask5": _cal_price(price, ask5, coefficient),
                        "bid_vol5": bid_vol5,
                        "ask_vol5": ask_vol5,
                        "trading_phase": trading_phase,
                        "reversed_bytes5": reversed_bytes5,
                        "reversed_bytes6": reversed_bytes6,
                        "reversed_bytes7": reversed_bytes7,
                        "reversed_bytes8": reversed_bytes8,
                        # ``reversed_bytes9`` historically exposed the scaled
                        # value.  Keep it as a compatibility alias while also
                        # publishing the verified name and signed wire value.
                        "reversed_bytes9": rate_raw / 100.0,
                        "rate": rate_raw / 100.0,
                        "rate_raw": rate_raw,
                        "active2": active2,
                        "volume": vol,
                    }
                )
        except (IndexError, struct.error) as exc:
            raise ProtocolDecodeError(f"failed to decode quotes row {len(rows)}") from exc

        return rows

    def encode_bars(self, frequency: int, market: int, code: str, start: int, count: int) -> bytes:
        encoded_code = code.encode("utf-8")
        values = (
            0x10C,
            0x01016408,
            0x1C,
            0x1C,
            0x052D,
            market,
            encoded_code,
            frequency,
            1,
            start,
            count,
            0,
            0,
            0,
        )
        return struct.pack("<HIHHHH6sHHHHIIH", *values)

    def decode_bars(self, body: bytes, frequency: int) -> list[dict[str, object]]:
        if len(body) < 2:
            raise ProtocolDecodeError(f"bars body too short: {len(body)}")

        try:
            (ret_count,) = U16_STRUCT.unpack_from(body, 0)
        except struct.error as exc:
            raise ProtocolDecodeError("failed to decode bars count") from exc

        pos = 2
        pre_diff_base = 0
        rows: list[dict[str, object]] = []

        try:
            for _ in range(ret_count):
                year, month, day, hour, minute, pos = _get_datetime(frequency, body, pos)
                price_open_diff, pos = _get_price(body, pos)
                price_close_diff, pos = _get_price(body, pos)
                price_high_diff, pos = _get_price(body, pos)
                price_low_diff, pos = _get_price(body, pos)
                (vol_raw,) = U32_STRUCT.unpack_from(body, pos)
                vol = _get_volume(vol_raw)
                if frequency in {0, 1, 2, 3, 4, 7, 8}:
                    # Standard security intraday bars encode volume in shares,
                    # while day-or-longer bars use lots.  Normalise the public
                    # ``vol``/``volume`` unit to lots across frequencies.
                    vol /= 100
                pos += 4
                (amount_raw,) = U32_STRUCT.unpack_from(body, pos)
                amount = _get_volume(amount_raw)
                pos += 4

                open_ = float(price_open_diff + pre_diff_base) / 1000
                price_open_diff = price_open_diff + pre_diff_base
                close = float(price_open_diff + price_close_diff) / 1000
                high = float(price_open_diff + price_high_diff) / 1000
                low = float(price_open_diff + price_low_diff) / 1000
                pre_diff_base = price_open_diff + price_close_diff

                previous_close = None if not rows else rows[-1]["close"]
                rows.append(
                    {
                        "open": open_,
                        "close": close,
                        "high": high,
                        "low": low,
                        "vol": vol,
                        "amount": amount,
                        "year": year,
                        "month": month,
                        "day": day,
                        "hour": hour,
                        "minute": minute,
                        "datetime": f"{year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}",
                        "volume": vol,
                        "previous_close": previous_close,
                    }
                )
        except (IndexError, struct.error) as exc:
            raise ProtocolDecodeError(f"failed to decode bars row {len(rows)}") from exc

        return rows

    def encode_index_bars(self, frequency: int, market: int, code: str, start: int, count: int) -> bytes:
        return self.encode_bars(frequency, market, code, start, count)

    def decode_index_bars(self, body: bytes, frequency: int) -> list[dict[str, object]]:
        if len(body) < 2:
            raise ProtocolDecodeError(f"index bars body too short: {len(body)}")

        try:
            (ret_count,) = U16_STRUCT.unpack_from(body, 0)
        except struct.error as exc:
            raise ProtocolDecodeError("failed to decode index bars count") from exc

        pos = 2
        pre_diff_base = 0
        rows: list[dict[str, object]] = []

        try:
            for _ in range(ret_count):
                year, month, day, hour, minute, pos = _get_datetime(frequency, body, pos)
                price_open_diff, pos = _get_price(body, pos)
                price_close_diff, pos = _get_price(body, pos)
                price_high_diff, pos = _get_price(body, pos)
                price_low_diff, pos = _get_price(body, pos)
                (vol_raw,) = U32_STRUCT.unpack_from(body, pos)
                decoded_volume = _get_volume(vol_raw)
                vol = decoded_volume
                if frequency not in {0, 1, 2, 3, 4, 7, 8}:
                    # Index day-or-longer bars encode volume at 1/100 of the
                    # public lot unit.
                    vol *= 100
                pos += 4
                (amount_raw,) = U32_STRUCT.unpack_from(body, pos)
                amount = _get_volume(amount_raw)
                pos += 4
                up_count, down_count = U16_PAIR_STRUCT.unpack_from(body, pos)
                pos += 4

                open_ = float(price_open_diff + pre_diff_base) / 1000
                price_open_diff = price_open_diff + pre_diff_base
                close = float(price_open_diff + price_close_diff) / 1000
                high = float(price_open_diff + price_high_diff) / 1000
                low = float(price_open_diff + price_low_diff) / 1000
                pre_diff_base = price_open_diff + price_close_diff

                previous_close = None if not rows else rows[-1]["close"]
                intraday_turnover_proxy = frequency in {0, 1, 2, 3, 7, 8}
                rows.append(
                    {
                        "open": open_,
                        "close": close,
                        "high": high,
                        "low": low,
                        "vol": vol,
                        "amount": amount,
                        "year": year,
                        "month": month,
                        "day": day,
                        "hour": hour,
                        "minute": minute,
                        "datetime": f"{year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}",
                        "up_count": up_count,
                        "down_count": down_count,
                        "volume": vol,
                        # Live Shanghai and Shenzhen index packets confirm
                        # that the intraday slot is approximately amount/100,
                        # not a share/lot volume. Keep ``vol``/``volume`` for
                        # wire and legacy compatibility, while publishing the
                        # semantics required for safe calculations.
                        "volume_raw": decoded_volume,
                        "volume_unit": (
                            "hundred_yuan_turnover" if intraday_turnover_proxy else "lot"
                        ),
                        "volume_lots": None if intraday_turnover_proxy else vol,
                        "turnover_100_yuan": vol if intraday_turnover_proxy else None,
                        "previous_close": previous_close,
                    }
                )
        except (IndexError, struct.error) as exc:
            raise ProtocolDecodeError(f"failed to decode index bars row {len(rows)}") from exc

        return rows

    def encode_minutes(self, market: int, code: str, date: str | int) -> bytes:
        if market not in self.security_markets:
            raise UnsupportedMarketError(f"unsupported market for minutes: {market}")

        if isinstance(date, str):
            date = int(date)

        encoded_code = code.encode("utf-8")
        payload = bytearray.fromhex("0c 01 30 00 01 01 0d 00 0d 00 b4 0f")
        payload.extend(struct.pack("<IB6s", date, market, encoded_code))
        return bytes(payload)

    def encode_call_auction(self, market: int, code: str) -> bytes:
        if market not in self.security_markets:
            raise UnsupportedMarketError(f"unsupported market for call_auction: {market}")
        encoded_code = code.encode("ascii")
        if len(encoded_code) != 6 or not code.isdigit():
            raise ValueError("call_auction requires a six-digit numeric code")

        payload = bytearray.fromhex("0c 00 00 00 00 01 1e 00 1e 00 6a 05")
        payload.extend(struct.pack("<BB6s", market, 0, encoded_code))
        payload.extend(bytes.fromhex("00 00 00 00 03 00 00 00 00 00 00 00 00 00 00 00 f4 01 00 00"))
        return bytes(payload)

    def decode_call_auction(self, body: bytes) -> list[dict[str, object]]:
        if len(body) < 2:
            raise ProtocolDecodeError(f"call_auction body too short: {len(body)}")
        try:
            (count,) = U16_STRUCT.unpack_from(body, 0)
        except struct.error as exc:
            raise ProtocolDecodeError("failed to decode call_auction count") from exc

        expected_size = 2 + count * CALL_AUCTION_ROW_STRUCT.size
        if len(body) != expected_size:
            raise ProtocolDecodeError(
                f"invalid call_auction body size: expected {expected_size} bytes, got {len(body)}"
            )

        rows: list[dict[str, object]] = []
        pos = 2
        for index in range(count):
            try:
                raw_time, price, matched, signed_unmatched, _, second = CALL_AUCTION_ROW_STRUCT.unpack_from(
                    body, pos
                )
            except struct.error as exc:
                raise ProtocolDecodeError(f"failed to decode call_auction row {index}") from exc
            pos += CALL_AUCTION_ROW_STRUCT.size
            hour, minute = divmod(raw_time, 60)
            if hour > 23 or second > 59 or not math.isfinite(price):
                raise ProtocolDecodeError(f"invalid call_auction row {index}")
            side = 1 if signed_unmatched > 0 else -1 if signed_unmatched < 0 else 0
            rows.append(
                {
                    "time": f"{hour:02d}:{minute:02d}:{second:02d}",
                    "hour": hour,
                    "minute": minute,
                    "second": second,
                    "price": round(float(price), 3),
                    "matched": matched,
                    "unmatched": abs(signed_unmatched),
                    "side": side,
                    "side_name": "buy" if side > 0 else "sell" if side < 0 else "balanced",
                }
            )
        return rows

    def decode_minutes(
        self,
        body: bytes,
        market: int,
        code: str,
        *,
        date: str | int | None = None,
        price_coefficient: float | None = None,
    ) -> list[dict[str, object]]:
        if len(body) < 2:
            raise ProtocolDecodeError(f"minutes body too short: {len(body)}")

        try:
            (num,) = U16_STRUCT.unpack_from(body, 0)
        except struct.error as exc:
            raise ProtocolDecodeError("failed to decode minutes count") from exc

        pos = 6
        last_price = 0
        coefficient = (
            _get_security_coefficient(market, code)
            if price_coefficient is None
            else float(price_coefficient)
        )
        rows: list[dict[str, object]] = []

        try:
            date_prefix = _format_yyyymmdd(date) if date is not None else None
            for index in range(num):
                price_raw, pos = _get_price(body, pos)
                _reversed_1, pos = _get_price(body, pos)
                vol, pos = _get_price(body, pos)
                last_price += price_raw
                price = float(last_price) * coefficient
                hour, minute = _minute_slot(index)
                time_value = f"{hour:02d}:{minute:02d}"
                row: dict[str, object] = {
                    "time": time_value,
                    "hour": hour,
                    "minute": minute,
                    "price": price,
                    "vol": vol,
                    "volume": vol,
                }
                if date_prefix is not None:
                    row["datetime"] = f"{date_prefix} {time_value}"
                rows.append(row)
        except (IndexError, struct.error) as exc:
            raise ProtocolDecodeError(f"failed to decode minutes row {len(rows)}") from exc

        return rows

    def encode_transaction(self, market: int, code: str, start: int, count: int) -> bytes:
        if market not in self.security_markets:
            raise UnsupportedMarketError(f"unsupported market for transaction: {market}")

        encoded_code = code.encode("utf-8")
        payload = bytearray.fromhex("0c 17 08 01 01 01 0e 00 0e 00 c5 0f")
        payload.extend(struct.pack("<H6sHH", market, encoded_code, start, count))
        return bytes(payload)

    def decode_transaction(
        self,
        body: bytes,
        *,
        market: int = 1,
        code: str = "600000",
        price_coefficient: float | None = None,
    ) -> list[dict[str, object]]:
        if len(body) < 2:
            raise ProtocolDecodeError(f"transaction body too short: {len(body)}")

        try:
            (num,) = U16_STRUCT.unpack_from(body, 0)
        except struct.error as exc:
            raise ProtocolDecodeError("failed to decode transaction count") from exc

        pos = 2
        last_price = 0
        rows: list[dict[str, object]] = []
        coefficient = (
            _get_security_coefficient(market, code)
            if price_coefficient is None
            else float(price_coefficient)
        )

        try:
            for _ in range(num):
                hour, minute, pos = _get_time(body, pos)
                price_raw, pos = _get_price(body, pos)
                vol, pos = _get_price(body, pos)
                num_trades, pos = _get_price(body, pos)
                buy_or_sell, pos = _get_price(body, pos)
                _reversed, pos = _get_price(body, pos)
                last_price += price_raw
                price = _scale_integer_price(last_price, coefficient)
                amount = price * vol * 100
                row: dict[str, object] = {
                    "time": f"{hour:02d}:{minute:02d}",
                    "price": price,
                    "vol": vol,
                    "num": num_trades,
                    "buyorsell": buy_or_sell,
                    "side_name": _transaction_side_name(buy_or_sell),
                    "is_buy": buy_or_sell == 0,
                    "is_sell": buy_or_sell == 1,
                    "amount": amount,
                    "average_volume": vol / num_trades if num_trades > 0 else None,
                    "average_amount": amount / num_trades if num_trades > 0 else None,
                    "volume": vol,
                }
                rows.append(row)
        except (IndexError, struct.error) as exc:
            raise ProtocolDecodeError(f"failed to decode transaction row {len(rows)}") from exc

        return rows

    def encode_history_transactions(
        self,
        market: int,
        code: str,
        start: int,
        count: int,
        date: str | int,
    ) -> bytes:
        if market not in self.security_markets:
            raise UnsupportedMarketError(f"unsupported market for transactions: {market}")

        if isinstance(date, str):
            date = int(date)

        encoded_code = code.encode("utf-8")
        payload = bytearray.fromhex("0c 01 30 01 00 01 12 00 12 00 b5 0f")
        payload.extend(struct.pack("<IH6sHH", date, market, encoded_code, start, count))
        return bytes(payload)

    def decode_history_transactions(
        self,
        body: bytes,
        *,
        market: int = 1,
        code: str = "600000",
        date: str | int | None = None,
        price_coefficient: float | None = None,
    ) -> list[dict[str, object]]:
        if len(body) < 6:
            raise ProtocolDecodeError(f"transactions body too short: {len(body)}")

        try:
            (num,) = U16_STRUCT.unpack_from(body, 0)
        except struct.error as exc:
            raise ProtocolDecodeError("failed to decode transactions count") from exc

        pos = 6
        last_price = 0
        rows: list[dict[str, object]] = []
        coefficient = (
            _get_security_coefficient(market, code)
            if price_coefficient is None
            else float(price_coefficient)
        )
        date_prefix = _format_yyyymmdd(date) if date is not None else None

        try:
            for _ in range(num):
                hour, minute, pos = _get_time(body, pos)
                price_raw, pos = _get_price(body, pos)
                vol, pos = _get_price(body, pos)
                buy_or_sell, pos = _get_price(body, pos)
                _reversed, pos = _get_price(body, pos)
                last_price += price_raw
                time_value = f"{hour:02d}:{minute:02d}"
                price = _scale_integer_price(last_price, coefficient)
                row: dict[str, object] = {
                    "time": time_value,
                    "price": price,
                    "vol": vol,
                    "buyorsell": buy_or_sell,
                    "side_name": _transaction_side_name(buy_or_sell),
                    "is_buy": buy_or_sell == 0,
                    "is_sell": buy_or_sell == 1,
                    "amount": price * vol * 100,
                    "volume": vol,
                }
                if date_prefix is not None:
                    row["datetime"] = f"{date_prefix} {time_value}"
                rows.append(row)
        except (IndexError, struct.error) as exc:
            raise ProtocolDecodeError(f"failed to decode transactions row {len(rows)}") from exc

        return rows

    def encode_finance(self, market: int, code: str) -> bytes:
        if market not in self.security_markets:
            raise UnsupportedMarketError(f"unsupported market for finance: {market}")

        encoded_code = code.encode("utf-8")
        payload = bytearray.fromhex("0c 1f 18 76 00 01 0b 00 0b 00 10 00 01 00")
        payload.extend(struct.pack("<B6s", market, encoded_code))
        return bytes(payload)

    def decode_finance(self, body: bytes) -> dict[str, object]:
        expected_size = 2 + 7 + FINANCE_BODY_STRUCT.size
        if len(body) < expected_size:
            raise ProtocolDecodeError(f"finance body too short: {len(body)}")

        pos = 2
        try:
            market, code = FINANCE_HEAD_STRUCT.unpack_from(body, pos)
        except struct.error as exc:
            raise ProtocolDecodeError("failed to decode finance header") from exc

        pos += 7
        try:
            (
                liutongguben,
                province,
                industry,
                updated_date,
                ipo_date,
                zongguben,
                guojiagu,
                faqirenfarengu,
                farengu,
                bgu,
                hgu,
                zhigonggu,
                zongzichan,
                liudongzichan,
                gudingzichan,
                wuxingzichan,
                gudongrenshu,
                liudongfuzhai,
                changqifuzhai,
                zibengongjijin,
                jingzichan,
                zhuyingshouru,
                zhuyinglirun,
                yingshouzhangkuan,
                yingyelirun,
                touzishouyu,
                jingyingxianjinliu,
                zongxianjinliu,
                cunhuo,
                lirunzonghe,
                shuihoulirun,
                jinglirun,
                weifenlirun,
                baoliu1,
                baoliu2,
            ) = FINANCE_BODY_STRUCT.unpack_from(body, pos)
        except struct.error as exc:
            raise ProtocolDecodeError("failed to decode finance body") from exc

        return {
            "market": market,
            "code": code.decode("utf-8"),
            "liutongguben": liutongguben * 10000,
            "province": province,
            "industry": industry,
            "updated_date": updated_date,
            "ipo_date": ipo_date,
            "zongguben": zongguben * 10000,
            "guojiagu": guojiagu * 10000,
            "faqirenfarengu": faqirenfarengu * 10000,
            "farengu": farengu * 10000,
            "bgu": bgu * 10000,
            "hgu": hgu * 10000,
            "zhigonggu": zhigonggu * 10000,
            "zongzichan": zongzichan * 10000,
            "liudongzichan": liudongzichan * 10000,
            "gudingzichan": gudingzichan * 10000,
            "wuxingzichan": wuxingzichan * 10000,
            "gudongrenshu": gudongrenshu,
            "liudongfuzhai": liudongfuzhai * 10000,
            "changqifuzhai": changqifuzhai * 10000,
            "zibengongjijin": zibengongjijin * 10000,
            "jingzichan": jingzichan * 10000,
            "zhuyingshouru": zhuyingshouru * 10000,
            "zhuyinglirun": zhuyinglirun * 10000,
            "yingshouzhangkuan": yingshouzhangkuan * 10000,
            "yingyelirun": yingyelirun * 10000,
            "touzishouyu": touzishouyu * 10000,
            "touzishouyi": touzishouyu * 10000,
            "jingyingxianjinliu": jingyingxianjinliu * 10000,
            "zongxianjinliu": zongxianjinliu * 10000,
            "cunhuo": cunhuo * 10000,
            "lirunzonghe": lirunzonghe * 10000,
            "shuihoulirun": shuihoulirun * 10000,
            "jinglirun": jinglirun * 10000,
            "weifenpeilirun": weifenlirun * 10000,
            "meigujingzichan": baoliu1,
            "baoliu2": baoliu2,
        }

    def encode_xdxr(self, market: int, code: str) -> bytes:
        if market not in {*self.valid_markets, MARKET_BJ}:
            raise UnsupportedMarketError(f"unsupported market for xdxr: {market}")

        encoded_code = code.encode("utf-8")
        payload = bytearray.fromhex("0c 1f 18 76 00 01 0b 00 0b 00 0f 00 01 00")
        payload.extend(struct.pack("<B6s", market, encoded_code))
        return bytes(payload)

    def decode_xdxr(self, body: bytes) -> list[dict[str, object]]:
        if len(body) < 11:
            return []

        pos = 9
        try:
            (num,) = U16_STRUCT.unpack_from(body, pos)
        except struct.error as exc:
            raise ProtocolDecodeError("failed to decode xdxr count") from exc
        pos += 2
        rows: list[dict[str, object]] = []

        try:
            for _ in range(num):
                market = body[pos]
                code = body[pos + 1 : pos + 7].decode("ascii", "ignore")
                record_reserved = body[pos + 7]
                pos += 8
                year, month, day, hour, minute, pos = _get_datetime(9, body, pos)
                category = body[pos]
                pos += 1
                suogu = None
                panqianliutong = None
                panhouliutong = None
                qianzongguben = None
                houzongguben = None
                songzhuangu = None
                fenhong = None
                peigu = None
                peigujia = None
                fenshu = None
                xingquanjia = None
                raw_c1: int | float
                raw_c2: int | float
                raw_c3: int | float
                raw_c4: int | float
                c1: int | float | None
                c2: int | float | None
                c3: int | float | None
                c4: int | float | None

                if category == 1:
                    raw_c1, raw_c2, raw_c3, raw_c4 = XDXR_FLOAT4_STRUCT.unpack_from(body, pos)
                    fenhong, peigujia, songzhuangu, peigu = raw_c1, raw_c2, raw_c3, raw_c4
                    c1, c2, c3, c4 = raw_c1, raw_c2, raw_c3, raw_c4
                elif category in {11, 12}:
                    raw_c1, raw_c2, raw_c3, raw_c4 = XDXR_MIXED_STRUCT.unpack_from(body, pos)
                    suogu = raw_c3
                    c1, c2, c3, c4 = raw_c1, raw_c2, raw_c3, raw_c4
                elif category in {13, 14}:
                    raw_c1, raw_c2, raw_c3, raw_c4 = XDXR_WARRANT_STRUCT.unpack_from(body, pos)
                    xingquanjia, fenshu = raw_c1, raw_c3
                    c1, c2, c3, c4 = raw_c1, raw_c2, raw_c3, raw_c4
                else:
                    raw_c1, raw_c2, raw_c3, raw_c4 = XDXR_UINT4_STRUCT.unpack_from(body, pos)
                    panqianliutong = 0 if raw_c1 == 0 else _get_volume(raw_c1)
                    qianzongguben = 0 if raw_c2 == 0 else _get_volume(raw_c2)
                    panhouliutong = 0 if raw_c3 == 0 else _get_volume(raw_c3)
                    houzongguben = 0 if raw_c4 == 0 else _get_volume(raw_c4)
                    c1 = panqianliutong * 10000
                    c2 = qianzongguben * 10000
                    c3 = panhouliutong * 10000
                    c4 = houzongguben * 10000

                pos += 16
                datetime_value = f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}"
                rows.append(
                    {
                        "market": market,
                        "code": code,
                        "symbol": f"{_market_prefix(market)}{code}",
                        "datetime": datetime_value,
                        "year": year,
                        "month": month,
                        "day": day,
                        "hour": hour,
                        "minute": minute,
                        "category": category,
                        "name": XDXR_CATEGORY_MAPPING.get(category, str(category)),
                        "record_reserved": record_reserved,
                        "raw_c1": raw_c1,
                        "raw_c2": raw_c2,
                        "raw_c3": raw_c3,
                        "raw_c4": raw_c4,
                        "c1": c1,
                        "c2": c2,
                        "c3": c3,
                        "c4": c4,
                        "fenhong": fenhong,
                        "peigujia": peigujia,
                        "songzhuangu": songzhuangu,
                        "peigu": peigu,
                        "suogu": suogu,
                        "panqianliutong": panqianliutong,
                        "panhouliutong": panhouliutong,
                        "qianzongguben": qianzongguben,
                        "houzongguben": houzongguben,
                        "panqianliutong_wan_shares": panqianliutong,
                        "panqianliutong_shares": None if panqianliutong is None else panqianliutong * 10000,
                        "panhouliutong_wan_shares": panhouliutong,
                        "panhouliutong_shares": None if panhouliutong is None else panhouliutong * 10000,
                        "qianzongguben_wan_shares": qianzongguben,
                        "qianzongguben_shares": None if qianzongguben is None else qianzongguben * 10000,
                        "houzongguben_wan_shares": houzongguben,
                        "houzongguben_shares": None if houzongguben is None else houzongguben * 10000,
                        "fenshu": fenshu,
                        "xingquanjia": xingquanjia,
                    }
                )
        except (IndexError, struct.error) as exc:
            raise ProtocolDecodeError(f"failed to decode xdxr row {len(rows)}") from exc

        return rows

    def encode_f10_categories(self, market: int, code: str) -> bytes:
        if market not in self.security_markets:
            raise UnsupportedMarketError(f"unsupported market for f10_categories: {market}")

        encoded_code = code.encode("utf-8")
        payload = bytearray.fromhex("0c 0f 10 9b 00 01 0e 00 0e 00 cf 02")
        payload.extend(struct.pack("<H6sI", market, encoded_code, 0))
        return bytes(payload)

    def decode_f10_categories(self, body: bytes) -> list[dict[str, object]]:
        if len(body) < 2:
            raise ProtocolDecodeError(f"f10_categories body too short: {len(body)}")

        try:
            (num,) = U16_STRUCT.unpack_from(body, 0)
        except struct.error as exc:
            raise ProtocolDecodeError("failed to decode f10_categories count") from exc

        pos = 2
        rows: list[dict[str, object]] = []
        try:
            for _ in range(num):
                name, filename, start, length = F10_CATEGORY_STRUCT.unpack_from(body, pos)
                pos += F10_CATEGORY_STRUCT.size
                rows.append(
                    {
                        "name": _decode_gbk_string(name),
                        "filename": _decode_gbk_string(filename),
                        "start": start,
                        "length": length,
                    }
                )
        except struct.error as exc:
            raise ProtocolDecodeError(f"failed to decode f10_categories row {len(rows)}") from exc

        return rows

    def encode_f10_content(
        self,
        market: int,
        code: str,
        filename: str | bytes,
        start: int,
        length: int,
    ) -> bytes:
        if market not in self.security_markets:
            raise UnsupportedMarketError(f"unsupported market for f10_content: {market}")

        encoded_code = code.encode("utf-8")
        encoded_filename = filename.encode("utf-8") if isinstance(filename, str) else filename
        if len(encoded_filename) > 80:
            raise ValueError("f10 filename must fit within 80 bytes")
        if len(encoded_filename) != 80:
            encoded_filename = encoded_filename.ljust(80, b"\x00")
        payload = bytearray.fromhex("0c 07 10 9c 00 01 68 00 68 00 d0 02")
        payload.extend(struct.pack("<H6sH80sIII", market, encoded_code, 0, encoded_filename, start, length, 0))
        return bytes(payload)

    def decode_f10_content_bytes(self, body: bytes) -> bytes:
        if len(body) < 12:
            raise ProtocolDecodeError(f"f10_content body too short: {len(body)}")

        try:
            _, length = F10_CONTENT_HEAD_STRUCT.unpack_from(body, 0)
        except struct.error as exc:
            raise ProtocolDecodeError("failed to decode f10_content header") from exc

        content = body[12 : 12 + length]
        if len(content) != length:
            raise ProtocolDecodeError(f"f10_content truncated: expected {length} bytes, got {len(content)}")
        return content

    def decode_f10_content(self, body: bytes) -> str:
        return self.decode_f10_content_bytes(body).decode("gbk", "ignore")

    def encode_block_info_meta(self, block_file: str) -> bytes:
        encoded = block_file.encode("utf-8")
        payload = bytearray.fromhex("0C 39 18 69 00 01 2A 00 2A 00 C5 02")
        payload.extend(struct.pack(f"<{0x2A - 2}s", encoded))
        return bytes(payload)

    def decode_block_info_meta(self, body: bytes) -> dict[str, object]:
        if len(body) < 38:
            raise ProtocolDecodeError(f"block info meta body too short: {len(body)}")

        try:
            size, _, hash_value, _ = BLOCK_INFO_META_STRUCT.unpack_from(body, 0)
        except struct.error as exc:
            raise ProtocolDecodeError("failed to decode block info meta") from exc

        return {"size": size, "hash_value": hash_value}

    def encode_block_info(self, block_file: str, start: int, size: int) -> bytes:
        encoded = block_file.encode("utf-8")
        payload = bytearray.fromhex("0c 37 18 6a 00 01 6e 00 6e 00 b9 06")
        payload.extend(struct.pack(f"<II{0x6E - 10}s", start, size, encoded))
        return bytes(payload)

    def decode_block_info(self, body: bytes) -> bytes:
        if len(body) < 4:
            raise ProtocolDecodeError(f"block info body too short: {len(body)}")

        try:
            (chunk_size,) = U32_STRUCT.unpack_from(body, 0)
        except struct.error as exc:
            raise ProtocolDecodeError("failed to decode block info chunk size") from exc

        chunk = body[4:]
        if chunk_size > len(chunk):
            raise ProtocolDecodeError(
                f"block info chunk truncated: expected {chunk_size} bytes, got {len(chunk)}"
            )
        return chunk[:chunk_size]


def _get_datetime(category: int, buffer: bytes, pos: int) -> tuple[int, int, int, int, int, int]:
    minute = 0
    hour = 15
    if category < 4 or category in {7, 8}:
        zip_day, minutes = ZIP_DAY_MINUTES_STRUCT.unpack_from(buffer, pos)
        month = int((zip_day % 2048) / 100)
        year = (zip_day >> 11) + 2004
        day = (zip_day % 2048) % 100
        minute = minutes % 60
        hour = int(minutes / 60)
    else:
        (zip_day,) = U32_STRUCT.unpack_from(buffer, pos)
        month = int((zip_day % 10000) / 100)
        year = int(zip_day / 10000)
        day = zip_day % 100

    return year, month, day, hour, minute, pos + 4


def _get_time(buffer: bytes, pos: int) -> tuple[int, int, int]:
    (minutes,) = U16_STRUCT.unpack_from(buffer, pos)
    hour = int(minutes / 60)
    minute = minutes % 60
    return hour, minute, pos + 2
