"""MAC 协议公开枚举和动态字段位图。"""

from __future__ import annotations

import math
import struct
from collections.abc import Iterable
from enum import Enum, IntEnum


UNUSUAL_TYPE_NAMES: dict[int, str] = {
    0x03: "主力买入卖出",
    0x04: "加速拉升",
    0x05: "加速下跌",
    0x06: "低位反弹",
    0x07: "高位回落",
    0x08: "撑杆跳高",
    0x09: "平台跳水",
    0x0A: "单笔冲涨跌",
    0x0B: "区间放量",
    0x0C: "区间缩量",
    0x10: "大单托盘",
    0x11: "大单压盘",
    0x12: "大单锁盘",
    0x13: "竞价试盘",
    0x14: "涨跌停",
    0x15: "竞价/尾盘异动",
    0x16: "盘中强势弱势",
    0x1D: "急速拉升",
    0x1E: "急速下跌",
}


class MacPeriod(IntEnum):
    MIN_5 = 0
    MIN_15 = 1
    MIN_30 = 2
    MIN_60 = 3
    DAY = 4
    WEEK = 5
    MONTH = 6
    MIN_1 = 7
    MINS = 8
    DAYS = 9
    QUARTER = 10
    YEAR = 11
    SECONDS = 13


class MacAdjust(IntEnum):
    NONE = 0
    QFQ = 1
    HFQ = 2


class MacCategory(IntEnum):
    SH = 0
    SZ = 2
    A = 6
    ALL = 6
    B = 7
    KCB = 8
    BJ = 12
    CYB = 14
    BOARD_ALL = 10000
    BOARD_HY = 10001
    BOARD_HY2 = 10002
    BOARD_GN = 10004
    BOARD_FG = 10005
    BOARD_DQ = 10006
    BOARD_OTHER = 10007
    BOARD_YJ_LEVEL1 = 10008
    BOARD_YJ_LEVEL2 = 10009
    BOARD_YJ_LEVEL3 = 10010
    HGT = 0x2AF9
    FXJS = 0x2AFF
    SGT = 0x2B01
    ETF = 0x2AFD
    LOF = 0x2B04
    ZS = 0x2B2C


class MacBoardType(IntEnum):
    HY = 0
    HY2 = 1
    GN = 3
    FG = 4
    DQ = 5
    OTHER = 6
    YJ_LEVEL1 = 7
    YJ_LEVEL2 = 8
    YJ_LEVEL3 = 9
    ALL = 255


class MacBoardSortColumn(IntEnum):
    """Sort columns supported by the MAC 0x1231 board-list command."""

    CHANGE_PCT = 0
    SPEED = 1
    CHANGE_3D = 2
    CHANGE_20D = 3
    CHANGE_60D = 4
    YTD = 5
    CHANGE_5D = 6
    CHANGE_10D = 7


class MacSortType(IntEnum):
    CODE = 0x00
    NAME = 0x01
    PRE_CLOSE = 0x02
    OPEN = 0x03
    HIGH = 0x04
    LOW = 0x05
    PRICE = 0x06
    BID = 0x07
    ASK = 0x08
    VOLUME = 0x09
    TOTAL_AMOUNT = 0x0A
    LAST_VOLUME = 0x0B
    CHANGE = 0x0C
    CHANGE_PCT = 0x0E
    AMPLITUDE_PCT = 0x0F
    AVG = 0x10
    PE_DYNAMIC = 0x11
    ENTRUST_RATIO = 0x12
    INSIDE_VOLUME = 0x13
    OUTSIDE_VOLUME = 0x14
    IN_OUT_RATIO = 0x15
    BID_VOLUME = 0x17
    ASK_VOLUME = 0x18
    LOCKED_RATIO = 0x1B
    LOCKED_AMOUNT = 0x1C
    OPEN_AMOUNT = 0x1D
    OPEN_TURNOVER_PCT = 0x1E
    VOL_RATIO = 0x23
    TURNOVER_RATE = 0x24
    FLOAT_SHARES = 0x25
    FLOAT_MARKET_CAP = 0x26
    TOTAL_MARKET_CAP_AB = 0x27
    UNMATCHED_VOLUME = 0x2A
    STRENGTH_PCT = 0x2D
    SPEED_PCT = 0x2E
    ACTIVITY = 0x2F
    SHORT_TURNOVER_PCT = 0xCC
    VOL_SPEED_PCT = 0xD0
    MAIN_NET_AMOUNT = 0xD4
    MAIN_NET_RATIO = 0xD7
    AMOUNT_2M = 0x10C
    AUCTION_LIMIT_BUY = 0x102
    OPEN_SNATCH_PCT = 0x10A
    OPEN_PCT = 0x119
    HIGH_PCT = 0x11A
    LOW_PCT = 0x11B
    AVG_CHANGE_PCT = 0x11C
    DRAWDOWN_PCT = 0x11E
    ATTACK_PCT = 0x11F


class MacSortOrder(IntEnum):
    NONE = 0
    DESC = 1
    ASC = 2


class MacFilter(IntEnum):
    NEW = 1
    KCB = 2
    KC = 2
    ST = 4
    CYB = 8
    CY = 8
    HK_CONNECT = 16
    BJ = 32
    APPROVAL = 64
    REGISTRATION = 128


class MacField(IntEnum):
    PRE_CLOSE = 0x00
    OPEN = 0x01
    HIGH = 0x02
    LOW = 0x03
    CLOSE = 0x04
    VOL = 0x05
    VOL_RATIO = 0x06
    AMOUNT = 0x07
    INSIDE_VOLUME = 0x08
    OUTSIDE_VOLUME = 0x09
    TOTAL_SHARES = 0x0A
    FLOAT_SHARES = 0x0B
    EPS = 0x0C
    NET_ASSETS = 0x0D
    SECURITY_TYPE_PRICE = 0x0E
    TOTAL_MARKET_CAP_AB = 0x0F
    PE_DYNAMIC = 0x10
    BID_PRICE = 0x11
    ASK_PRICE = 0x12
    SERVER_UPDATE_DATE = 0x13
    SERVER_UPDATE_TIME = 0x14
    LOT_SIZE_INFO = 0x15
    BOARD_STRENGTH = 0x16
    DIVIDEND_YIELD = 0x17
    BID_VOLUME = 0x18
    ASK_VOLUME = 0x19
    LAST_VOLUME = 0x1A
    TURNOVER = 0x1B
    INDUSTRY = 0x1C
    INDUSTRY_CHANGE_UP = 0x1D
    STOCK_TAG_FLAGS = 0x1E
    DECIMAL_POINT = 0x1F
    BUY_PRICE_LIMIT = 0x20
    SELL_PRICE_LIMIT = 0x21
    PRICE_DECIMAL_INFO = 0x22
    LOT_SIZE = 0x23
    PRE_IOPV = 0x24
    SPEED_PCT = 0x25
    AVG_PRICE = 0x26
    IOPV = 0x27
    PE_TTM_VOL_RELATED = 0x28
    EX_PRICE_PLACEHOLDER = 0x29
    OPERATING_REVENUE = 0x2A
    FLAG_KCB = 0x2B
    FLAG_BJ = 0x2C
    CIRCULATING_CAPITAL_Z = 0x2D
    AFTER_HOURS_VOLUME = 0x2E
    PE_TTM = 0x30
    PE_STATIC = 0x31
    INDEX_METRIC = 0x37
    MAIN_NET_AMOUNT = 0x38
    BID_ASK_RATIO = 0x39
    NON_INDEX_FLAG = 0x3A
    CHANGE_20D_PCT = 0x3B
    YTD_PCT = 0x3C
    STOCK_CLASS_CODE = 0x3E
    PERCENT_BASE = 0x3F
    MTD_PCT = 0x40
    CHANGE_1Y_PCT = 0x41
    PREV_CHANGE_PCT = 0x42
    CHANGE_3D_PCT = 0x43
    CHANGE_60D_PCT = 0x44
    CHANGE_5D_PCT = 0x45
    CHANGE_10D_PCT = 0x46
    PREV2_CHANGE_PCT = 0x47
    BID2_PRICE = 0x48
    ASK2_PRICE = 0x49
    AH_CODE = 0x4A
    UNKNOWN_CODE = 0x4B
    OPEN_AMOUNT = 0x57
    ANNUAL_LIMIT_UP_DAYS = 0x58
    ACTIVITY = 0x59
    DIVIDEND_YIELD_RATE = 0x5B
    CONSECUTIVE_UP_DAYS = 0x5C
    LIMIT_UP_COUNT = 0x5D
    BID2_VOLUME = 0x5D
    LIMIT_DOWN_COUNT = 0x5E
    ASK2_VOLUME = 0x5E
    INDUSTRY_SUB = 0x5F
    AUCTION_BUY_LIMIT = 0x66
    AUCTION_SELL_LIMIT = 0x67
    VOL_SPEED_PCT = 0x68
    SHORT_TURNOVER_PCT = 0x69
    AMOUNT_2M = 0x6A
    MAIN_NET_AMOUNT_COPY = 0x6B
    MAIN_NET_RATIO = 0x6C
    RETAIL_NET_AMOUNT = 0x6D
    MAIN_NET_5M_AMOUNT = 0x6E
    MAIN_NET_3D_AMOUNT = 0x6F
    MAIN_NET_5D_AMOUNT = 0x70
    MAIN_NET_10D_AMOUNT = 0x71
    MAIN_BUY_NET_AMOUNT = 0x72
    DDX = 0x73
    DDY = 0x74
    DDZ = 0x75
    DDF = 0x76
    STOCK_FLAG_A = 0x77
    STOCK_FLAG_B = 0x78
    AUCTION_VOL_RATIO = 0x7A
    PREV_AMOUNT = 0x7B
    RECENT_INDICATOR = 0x7D
    BID3_PRICE = 0x80
    BID4_PRICE = 0x81
    BID5_PRICE = 0x82
    ASK3_PRICE = 0x83
    ASK4_PRICE = 0x84
    ASK5_PRICE = 0x85
    BID3_VOLUME = 0x86
    BID4_VOLUME = 0x87
    UP_COUNT = 0x88
    BID5_VOLUME = 0x88
    ASK3_VOLUME = 0x89
    ASK4_VOLUME = 0x8A
    DOWN_COUNT = 0x8B
    ASK5_VOLUME = 0x8B
    BID_ASK_DIFF = 0x8C
    CHANGE_UP_TYPE = 0x8D
    SAFETY_SCORE = 0x8E
    HIGHLIGHT_COUNT = 0x8F
    CHANGE_AT_1000 = 0x90
    CHANGE_AT_1030 = 0x91
    CHANGE_AT_1100 = 0x92
    CHANGE_AT_1130 = 0x93
    CHANGE_AT_1330 = 0x94
    CHANGE_AT_1400 = 0x95
    CHANGE_AT_1430 = 0x96

    def __add__(self, other: object):
        if isinstance(other, (MacField, MacFieldPreset, MacFieldSelection)):
            return MacFieldSelection(self, other)
        return NotImplemented

    def __or__(self, other: object):
        return self.__add__(other)

    def __radd__(self, other: object):
        if isinstance(other, (MacField, MacFieldPreset, MacFieldSelection)):
            return MacFieldSelection(other, self)
        return NotImplemented

    def __ror__(self, other: object):
        return self.__radd__(other)

    @property
    def field_name(self) -> str:
        return self.name.lower()

    @property
    def fmt(self) -> str:
        integer = self in {
            MacField.VOL, MacField.INSIDE_VOLUME, MacField.OUTSIDE_VOLUME,
            MacField.BID_VOLUME, MacField.ASK_VOLUME, MacField.LAST_VOLUME,
            MacField.INDUSTRY, MacField.FLAG_KCB, MacField.FLAG_BJ,
            MacField.SERVER_UPDATE_DATE, MacField.SERVER_UPDATE_TIME,
            MacField.LOT_SIZE_INFO, MacField.STOCK_TAG_FLAGS, MacField.DECIMAL_POINT,
            MacField.PRICE_DECIMAL_INFO, MacField.LOT_SIZE, MacField.NON_INDEX_FLAG,
            MacField.STOCK_CLASS_CODE, MacField.PERCENT_BASE, MacField.INDUSTRY_SUB,
            MacField.AH_CODE, MacField.UNKNOWN_CODE, MacField.ACTIVITY,
            MacField.LIMIT_UP_COUNT, MacField.LIMIT_DOWN_COUNT,
            MacField.BID3_VOLUME, MacField.BID4_VOLUME, MacField.UP_COUNT,
            MacField.ASK3_VOLUME, MacField.ASK4_VOLUME, MacField.DOWN_COUNT,
        }
        signed = self in {
            MacField.AFTER_HOURS_VOLUME,
            MacField.ANNUAL_LIMIT_UP_DAYS,
            MacField.CONSECUTIVE_UP_DAYS,
            MacField.BID_ASK_DIFF,
            MacField.CHANGE_UP_TYPE,
        }
        return "<i" if signed else "<I" if integer else "<f"


class MacFieldPreset(Enum):
    NONE = ()
    OHLC = (MacField.OPEN, MacField.HIGH, MacField.LOW, MacField.CLOSE)
    BASIC = OHLC + (MacField.PRE_CLOSE, MacField.VOL)
    QUOTE = (
        MacField.BID_PRICE,
        MacField.ASK_PRICE,
        MacField.BID_VOLUME,
        MacField.ASK_VOLUME,
        MacField.LAST_VOLUME,
    )
    VOLUME = (MacField.VOL, MacField.AMOUNT, MacField.TURNOVER, MacField.VOL_RATIO)
    FUNDAMENTAL = (MacField.TOTAL_SHARES, MacField.FLOAT_SHARES, MacField.EPS, MacField.NET_ASSETS)
    FUND_FLOW = (
        MacField.MAIN_NET_AMOUNT,
        MacField.MAIN_NET_RATIO,
        MacField.MAIN_NET_5M_AMOUNT,
        MacField.MAIN_NET_3D_AMOUNT,
        MacField.MAIN_NET_5D_AMOUNT,
        MacField.MAIN_NET_10D_AMOUNT,
        MacField.MAIN_BUY_NET_AMOUNT,
    )
    ENHANCED = (
        MacField.OPEN,
        MacField.HIGH,
        MacField.LOW,
        MacField.CLOSE,
        MacField.VOL,
        MacField.FLOAT_SHARES,
        MacField.ACTIVITY,
    )
    AH_CODE_FIELDS = (
        MacField.OPEN,
        MacField.HIGH,
        MacField.LOW,
        MacField.CLOSE,
        MacField.VOL,
        MacField.AH_CODE,
        MacField.LOT_SIZE,
        MacField.INDUSTRY,
    )
    BOARD_STATS = (
        MacField.LIMIT_UP_COUNT,
        MacField.LIMIT_DOWN_COUNT,
        MacField.UP_COUNT,
        MacField.DOWN_COUNT,
    )
    HANDICAP = (
        MacField.BID_PRICE, MacField.BID2_PRICE, MacField.BID3_PRICE,
        MacField.BID4_PRICE, MacField.BID5_PRICE,
        MacField.ASK_PRICE, MacField.ASK2_PRICE, MacField.ASK3_PRICE,
        MacField.ASK4_PRICE, MacField.ASK5_PRICE,
        MacField.BID_VOLUME, MacField.BID2_VOLUME, MacField.BID3_VOLUME,
        MacField.BID4_VOLUME, MacField.BID5_VOLUME,
        MacField.ASK_VOLUME, MacField.ASK2_VOLUME, MacField.ASK3_VOLUME,
        MacField.ASK4_VOLUME, MacField.ASK5_VOLUME,
    )
    COMMON = (
        MacField.PRE_CLOSE, MacField.OPEN, MacField.HIGH, MacField.LOW,
        MacField.CLOSE, MacField.VOL, MacField.VOL_RATIO, MacField.AMOUNT,
        MacField.TOTAL_SHARES, MacField.FLOAT_SHARES, MacField.EPS,
        MacField.NET_ASSETS, MacField.SECURITY_TYPE_PRICE,
        MacField.TOTAL_MARKET_CAP_AB, MacField.PE_DYNAMIC, MacField.LOT_SIZE_INFO,
        MacField.DIVIDEND_YIELD, MacField.LAST_VOLUME, MacField.TURNOVER,
        MacField.STOCK_TAG_FLAGS, MacField.DECIMAL_POINT,
        MacField.BUY_PRICE_LIMIT, MacField.SELL_PRICE_LIMIT,
        MacField.PRICE_DECIMAL_INFO, MacField.LOT_SIZE, MacField.PRE_IOPV,
        MacField.SPEED_PCT, MacField.FLAG_KCB, MacField.PE_TTM, MacField.PE_STATIC,
        MacField.MAIN_NET_AMOUNT, MacField.VOL_SPEED_PCT,
        MacField.SHORT_TURNOVER_PCT, MacField.CIRCULATING_CAPITAL_Z,
    )
    ALL = tuple(MacField)
    DEBUG = ALL

    def __add__(self, other: object):
        if isinstance(other, (MacField, MacFieldPreset, MacFieldSelection)):
            return MacFieldSelection(self, other)
        return NotImplemented

    def __or__(self, other: object):
        return self.__add__(other)

    def __radd__(self, other: object):
        if isinstance(other, (MacField, MacFieldPreset, MacFieldSelection)):
            return MacFieldSelection(other, self)
        return NotImplemented

    def __ror__(self, other: object):
        return self.__radd__(other)


class MacFieldSelection:
    """去重、排序后的 MAC 字段选择。支持 20 字节完整位图。"""

    def __init__(self, *fields: MacField | MacFieldPreset | Iterable[MacField]) -> None:
        values: list[MacField] = []
        for item in fields:
            if isinstance(item, MacFieldPreset):
                values.extend(item.value)
            elif isinstance(item, MacField):
                values.append(item)
            else:
                values.extend(item)
        self.fields = tuple(sorted(set(values), key=lambda value: value.value))

    def bitmap(self) -> bytes:
        value = 0
        for field in self.fields:
            value |= 1 << int(field)
        return value.to_bytes(20, "little")

    def __iter__(self):
        return iter(self.fields)

    def __contains__(self, item: object) -> bool:
        return item in self.fields

    def __or__(self, other: MacField | MacFieldPreset | Iterable[MacField] | MacFieldSelection) -> MacFieldSelection:
        """Return a selection containing fields from both operands."""
        if isinstance(other, MacFieldSelection):
            return MacFieldSelection(self.fields, other.fields)
        return MacFieldSelection(self.fields, other)

    def __add__(self, other: MacField | MacFieldPreset | Iterable[MacField] | MacFieldSelection) -> MacFieldSelection:
        return self.__or__(other)

    def __ror__(self, other: MacField | MacFieldPreset | Iterable[MacField]) -> MacFieldSelection:
        return MacFieldSelection(other, self.fields)

    def __radd__(self, other: MacField | MacFieldPreset | Iterable[MacField]) -> MacFieldSelection:
        return self.__ror__(other)


def normalize_mac_fields(fields: object | None) -> MacFieldSelection:
    if fields is None:
        return MacFieldSelection(MacFieldPreset.COMMON)
    if isinstance(fields, MacFieldSelection):
        return fields
    if isinstance(fields, (MacField, MacFieldPreset)):
        return MacFieldSelection(fields)
    return MacFieldSelection(fields)  # type: ignore[arg-type]


def active_mac_fields(bitmap: bytes) -> list[MacField]:
    value = int.from_bytes(bitmap[:20], "little")
    result: list[MacField] = []
    while value:
        low = value & -value
        bit = low.bit_length() - 1
        value ^= low
        try:
            result.append(MacField(bit))
        except ValueError:
            continue
    return result


def mac_field_value(data: bytes, field: MacField) -> object:
    value = struct.unpack(field.fmt, data)[0]
    if field is MacField.BID_ASK_DIFF:
        # Older easy_tdx decoders documented this slot as ``<i``.  Current
        # 7709 nodes send the same semantic value as an IEEE-754 float
        # (e.g. 43.0 -> 00 00 2c 42).  Keep accepting the historical integer
        # representation while normalizing the live float representation.
        as_float = struct.unpack("<f", data)[0]
        if abs(value) > 1_000_000 and math.isfinite(as_float) and abs(as_float) < 1_000_000:
            return as_float
    return value


__all__ = [
    "MacAdjust", "MacBoardType", "MacCategory", "MacField", "MacFieldPreset",
    "MacFieldSelection", "MacFilter", "MacPeriod", "MacSortOrder", "MacSortType",
    "UNUSUAL_TYPE_NAMES", "active_mac_fields", "mac_field_value", "normalize_mac_fields",
]
