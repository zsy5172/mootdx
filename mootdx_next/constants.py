from __future__ import annotations

MARKET_SZ = 0
MARKET_SH = 1
MARKET_BJ = 2

KLINE_5MIN = 0
KLINE_15MIN = 1
KLINE_30MIN = 2
KLINE_1HOUR = 3
KLINE_DAILY = 4
KLINE_WEEKLY = 5
KLINE_MONTHLY = 6
KLINE_EX_1MIN = 7
KLINE_1MIN = 8
KLINE_RI_K = 9
KLINE_3MONTH = 10
KLINE_YEARLY = 11

FREQUENCIES = (
    "5m",
    "15m",
    "30m",
    "1h",
    "day",
    "week",
    "mon",
    "ex_1m",
    "1m",
    "dk",
    "3mon",
    "year",
)

MAX_KLINE_COUNT = 800
MAX_TRANSACTION_COUNT = 1800
MAX_HISTORY_TRANSACTION_COUNT = 2000

BLOCK_SZ = "block_zs.dat"
BLOCK_FG = "block_fg.dat"
BLOCK_GN = "block_gn.dat"
BLOCK_DEFAULT = "block.dat"
TYPE_FLATS = 0
TYPE_GROUP = 1

# Each endpoint below returned and decoded both a realtime quote and daily bars
# for 600036 during the 2026-07-20 capability audit. CandidateRegistry probes
# the capabilities again before exposing a latency-ranked snapshot.
HQ_HOSTS = (
    ("深圳双线主站1", "110.41.147.114", 7709),
    ("上海双线主站6", "124.70.199.56", 7709),
    ("上海双线主站9", "121.36.225.169", 7709),
    ("上海双线主站10", "123.60.70.228", 7709),
    ("上海双线主站11", "123.60.73.44", 7709),
    ("上海双线主站12", "124.70.133.119", 7709),
    ("上海双线主站13", "124.71.187.72", 7709),
    ("上海双线主站14", "124.71.187.122", 7709),
    ("武汉电信主站1", "119.97.185.59", 7709),
    ("广州双线主站4", "124.71.9.153", 7709),
    ("上海双线主站15", "123.60.84.66", 7709),
    ("广州双线主站5", "116.205.163.254", 7709),
    ("广州双线主站6", "116.205.171.132", 7709),
    ("广州双线主站7", "116.205.183.150", 7709),
    ("通达信深圳双线主站2", "110.41.2.72", 7709),
    ("通达信深圳双线主站4", "175.178.112.197", 7709),
    ("通达信深圳双线主站5", "175.178.128.227", 7709),
    ("通达信上海双线主站2", "122.51.120.217", 7709),
    ("通达信上海双线主站4", "123.60.164.122", 7709),
    ("通达信上海双线主站5", "111.229.247.189", 7709),
    ("通达信上海双线主站7", "122.51.232.182", 7709),
    ("通达信上海双线主站8", "118.25.98.114", 7709),
    ("通达信深圳双线主站7", "129.204.230.128", 7709),
    ("通达信深圳双线主站8", "111.230.186.52", 7709),
    ("上海双线主站16", "121.37.183.82", 7709),
    ("深圳双线主站9", "110.41.174.169", 7709),
    ("补充行情主站1", "182.140.139.191", 7709),
    ("补充行情主站2", "119.6.200.40", 7709),
    ("补充行情主站3", "218.200.222.134", 7709),
    ("补充行情主站4", "182.150.28.166", 7709),
)

EX_HOSTS = (
    ("银河阿里云扩展行情", "47.112.95.207", 7720),
    ("银河杭州电信扩展行情", "218.75.75.18", 7720),
    ("银河武汉电信扩展行情", "58.49.110.76", 7720),
)

FINANCIAL_BASE_URL = "https://data.tdx.com.cn/tdxfin"
FINANCIAL_CATALOG_FILE = "gpcw.txt"
