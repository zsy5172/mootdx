from __future__ import annotations

import struct
from typing import Any

from mootdx_next.constants import MARKET_BJ
from mootdx_next.errors import ProtocolDecodeError
from mootdx_next.errors import UnsupportedMarketError
from mootdx_next.interfaces import AbstractProtocol
from mootdx_next.models import ResponseEnvelope

SECURITY_COEFFICIENT = {
    "SH_A_STOCK": 0.01,
    "SH_B_STOCK": 0.001,
    "SH_INDEX": 0.01,
    "SH_FUND": 0.001,
    "SH_BOND": 0.0001,
    "SZ_A_STOCK": 0.01,
    "SZ_B_STOCK": 0.01,
    "SZ_INDEX": 0.01,
    "SZ_FUND": 0.001,
    "SZ_BOND": 0.0001,
}

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


def _get_volume(vol: int) -> float:
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
    code_head = str(code)[:2]

    if market == 0:
        if code_head in ["00", "30"]:
            return "SZ_A_STOCK"
        if code_head in ["20"]:
            return "SZ_B_STOCK"
        if code_head in ["39"]:
            return "SZ_INDEX"
        if code_head in ["15", "16"]:
            return "SZ_FUND"
        if code_head in ["10", "11", "12", "13", "14"]:
            return "SZ_BOND"

    if market == 1:
        if code_head in ["60", "68"]:
            return "SH_A_STOCK"
        if code_head in ["90"]:
            return "SH_B_STOCK"
        if code_head in ["00", "88", "99"]:
            return "SH_INDEX"
        if code_head in ["50", "51"]:
            return "SH_FUND"
        if code_head in ["01", "10", "11", "12", "13", "14", "20"]:
            return "SH_BOND"

    raise NotImplementedError


def _get_security_coefficient(market: int, code: str) -> float:
    if market == MARKET_BJ:
        return 0.01

    try:
        return SECURITY_COEFFICIENT[_get_security_type(market=market, code=code)]
    except NotImplementedError:
        return 0.01


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


def _decode_gbk_string(value: bytes) -> str:
    zero = value.find(b"\x00")
    if zero != -1:
        value = value[:zero]
    return value.decode("gbk", "ignore")


class StdQuoteProtocol(AbstractProtocol):
    valid_markets = {0, 1}
    stock_count_markets = {0, 1, 2}
    quote_markets = {0, 1, 2}

    def encode(self, api: str, **kwargs: Any) -> bytes:
        if api == "stock_count":
            return self.encode_stock_count(int(kwargs["market"]))
        if api == "stock_list_page":
            return self.encode_stock_list_page(int(kwargs["market"]), int(kwargs["start"]))
        if api == "quotes":
            return self.encode_quotes(list(kwargs["symbols"]))
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
            return self.decode_quotes(body)
        if api == "bars":
            return self.decode_bars(body, int(kwargs["frequency"]))
        if api == "index_bars":
            return self.decode_index_bars(body, int(kwargs["frequency"]))
        if api == "minutes":
            return self.decode_minutes(body, int(kwargs["market"]), str(kwargs["code"]))
        if api == "transaction":
            return self.decode_transaction(body)
        if api == "transactions":
            return self.decode_history_transactions(body)
        if api == "finance":
            return self.decode_finance(body)
        if api == "xdxr":
            return self.decode_xdxr(body)
        if api == "f10_categories":
            return self.decode_f10_categories(body)
        if api == "f10_content":
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
                    "name": name_bytes.decode("gbk", errors="ignore"),
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

    def decode_quotes(self, body: bytes) -> list[dict[str, object]]:
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
                reversed_bytes9, active2 = QUOTE_TAIL_STRUCT.unpack_from(body, pos)
                pos += 4

                decoded_code = code.decode("utf-8")
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
                        "reversed_bytes9": reversed_bytes9 / 100.0,
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
                vol = _get_volume(vol_raw)
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
                    }
                )
        except (IndexError, struct.error) as exc:
            raise ProtocolDecodeError(f"failed to decode index bars row {len(rows)}") from exc

        return rows

    def encode_minutes(self, market: int, code: str, date: str | int) -> bytes:
        if market not in self.valid_markets:
            raise UnsupportedMarketError(f"unsupported market for minutes: {market}")

        if isinstance(date, str):
            date = int(date)

        encoded_code = code.encode("utf-8")
        payload = bytearray.fromhex("0c 01 30 00 01 01 0d 00 0d 00 b4 0f")
        payload.extend(struct.pack("<IB6s", date, market, encoded_code))
        return bytes(payload)

    def decode_minutes(self, body: bytes, market: int, code: str) -> list[dict[str, object]]:
        if len(body) < 2:
            raise ProtocolDecodeError(f"minutes body too short: {len(body)}")

        try:
            (num,) = U16_STRUCT.unpack_from(body, 0)
        except struct.error as exc:
            raise ProtocolDecodeError("failed to decode minutes count") from exc

        pos = 6
        last_price = 0
        coefficient = _get_security_coefficient(market, code)
        rows: list[dict[str, object]] = []

        try:
            for _ in range(num):
                price_raw, pos = _get_price(body, pos)
                _reversed_1, pos = _get_price(body, pos)
                vol, pos = _get_price(body, pos)
                last_price += price_raw
                price = float(last_price) * coefficient
                rows.append({"price": price, "vol": vol, "volume": vol})
        except (IndexError, struct.error) as exc:
            raise ProtocolDecodeError(f"failed to decode minutes row {len(rows)}") from exc

        return rows

    def encode_transaction(self, market: int, code: str, start: int, count: int) -> bytes:
        if market not in self.valid_markets:
            raise UnsupportedMarketError(f"unsupported market for transaction: {market}")

        encoded_code = code.encode("utf-8")
        payload = bytearray.fromhex("0c 17 08 01 01 01 0e 00 0e 00 c5 0f")
        payload.extend(struct.pack("<H6sHH", market, encoded_code, start, count))
        return bytes(payload)

    def decode_transaction(self, body: bytes) -> list[dict[str, object]]:
        if len(body) < 2:
            raise ProtocolDecodeError(f"transaction body too short: {len(body)}")

        try:
            (num,) = U16_STRUCT.unpack_from(body, 0)
        except struct.error as exc:
            raise ProtocolDecodeError("failed to decode transaction count") from exc

        pos = 2
        last_price = 0
        rows: list[dict[str, object]] = []

        try:
            for _ in range(num):
                hour, minute, pos = _get_time(body, pos)
                price_raw, pos = _get_price(body, pos)
                vol, pos = _get_price(body, pos)
                num_trades, pos = _get_price(body, pos)
                buy_or_sell, pos = _get_price(body, pos)
                _reversed, pos = _get_price(body, pos)
                last_price += price_raw
                rows.append(
                    {
                        "time": f"{hour:02d}:{minute:02d}",
                        "price": float(last_price) / 100,
                        "vol": vol,
                        "num": num_trades,
                        "buyorsell": buy_or_sell,
                        "volume": vol,
                    }
                )
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
        if market not in self.valid_markets:
            raise UnsupportedMarketError(f"unsupported market for transactions: {market}")

        if isinstance(date, str):
            date = int(date)

        encoded_code = code.encode("utf-8")
        payload = bytearray.fromhex("0c 01 30 01 00 01 12 00 12 00 b5 0f")
        payload.extend(struct.pack("<IH6sHH", date, market, encoded_code, start, count))
        return bytes(payload)

    def decode_history_transactions(self, body: bytes) -> list[dict[str, object]]:
        if len(body) < 6:
            raise ProtocolDecodeError(f"transactions body too short: {len(body)}")

        try:
            (num,) = U16_STRUCT.unpack_from(body, 0)
        except struct.error as exc:
            raise ProtocolDecodeError("failed to decode transactions count") from exc

        pos = 6
        last_price = 0
        rows: list[dict[str, object]] = []

        try:
            for _ in range(num):
                hour, minute, pos = _get_time(body, pos)
                price_raw, pos = _get_price(body, pos)
                vol, pos = _get_price(body, pos)
                buy_or_sell, pos = _get_price(body, pos)
                _reversed, pos = _get_price(body, pos)
                last_price += price_raw
                rows.append(
                    {
                        "time": f"{hour:02d}:{minute:02d}",
                        "price": float(last_price) / 100,
                        "vol": vol,
                        "buyorsell": buy_or_sell,
                        "volume": vol,
                    }
                )
        except (IndexError, struct.error) as exc:
            raise ProtocolDecodeError(f"failed to decode transactions row {len(rows)}") from exc

        return rows

    def encode_finance(self, market: int, code: str) -> bytes:
        if market not in self.valid_markets:
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
        if market not in self.valid_markets:
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

                if category == 1:
                    fenhong, peigujia, songzhuangu, peigu = XDXR_FLOAT4_STRUCT.unpack_from(body, pos)
                elif category in {11, 12}:
                    _, _, suogu, _ = XDXR_MIXED_STRUCT.unpack_from(body, pos)
                elif category in {13, 14}:
                    xingquanjia, _, fenshu, _ = XDXR_WARRANT_STRUCT.unpack_from(body, pos)
                else:
                    panqian_raw, qianzong_raw, panhou_raw, houzong_raw = XDXR_UINT4_STRUCT.unpack_from(body, pos)
                    panqianliutong = 0 if panqian_raw == 0 else _get_volume(panqian_raw)
                    panhouliutong = 0 if panhou_raw == 0 else _get_volume(panhou_raw)
                    qianzongguben = 0 if qianzong_raw == 0 else _get_volume(qianzong_raw)
                    houzongguben = 0 if houzong_raw == 0 else _get_volume(houzong_raw)

                pos += 16
                rows.append(
                    {
                        "year": year,
                        "month": month,
                        "day": day,
                        "category": category,
                        "name": XDXR_CATEGORY_MAPPING.get(category, str(category)),
                        "fenhong": fenhong,
                        "peigujia": peigujia,
                        "songzhuangu": songzhuangu,
                        "peigu": peigu,
                        "suogu": suogu,
                        "panqianliutong": panqianliutong,
                        "panhouliutong": panhouliutong,
                        "qianzongguben": qianzongguben,
                        "houzongguben": houzongguben,
                        "fenshu": fenshu,
                        "xingquanjia": xingquanjia,
                    }
                )
        except (IndexError, struct.error) as exc:
            raise ProtocolDecodeError(f"failed to decode xdxr row {len(rows)}") from exc

        return rows

    def encode_f10_categories(self, market: int, code: str) -> bytes:
        if market not in self.valid_markets:
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
        if market not in self.valid_markets:
            raise UnsupportedMarketError(f"unsupported market for f10_content: {market}")

        encoded_code = code.encode("utf-8")
        encoded_filename = filename.encode("utf-8") if isinstance(filename, str) else filename
        if len(encoded_filename) != 80:
            encoded_filename = encoded_filename.ljust(80, b"\x00")
        payload = bytearray.fromhex("0c 07 10 9c 00 01 68 00 68 00 d0 02")
        payload.extend(struct.pack("<H6sH80sIII", market, encoded_code, 0, encoded_filename, start, length, 0))
        return bytes(payload)

    def decode_f10_content(self, body: bytes) -> str:
        if len(body) < 12:
            raise ProtocolDecodeError(f"f10_content body too short: {len(body)}")

        try:
            _, length = F10_CONTENT_HEAD_STRUCT.unpack_from(body, 0)
        except struct.error as exc:
            raise ProtocolDecodeError("failed to decode f10_content header") from exc

        content = body[12 : 12 + length]
        if len(content) != length:
            raise ProtocolDecodeError(f"f10_content truncated: expected {length} bytes, got {len(content)}")
        return content.decode("gbk", "ignore")

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

        return body[4:]


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
