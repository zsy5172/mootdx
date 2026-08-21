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

# A request context uses this timeout unless a caller supplies a more
# specific value.  Public Pandas compatibility clients express their timeout
# in seconds and convert it to this unit when constructing a native client.
DEFAULT_REQUEST_TIMEOUT_MS = 15_000

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
MAX_QUOTE_COUNT = 80
MAX_TRANSACTION_COUNT = 1800
MAX_HISTORY_TRANSACTION_COUNT = 2000
MAX_LIMIT_PRICE_COUNT = 2000

# Live ExHq nodes cap a single K-line response at 700 rows.  The transaction
# defaults match the upstream TDX client protocol and keep the response below
# the 16-bit wire-frame length limit.
MAX_EX_KLINE_COUNT = 700
MAX_EX_TRANSACTION_COUNT = 1800
MAX_EX_INSTRUMENT_COUNT = 1000
MAX_EX_QUOTE_LIST_COUNT = 100

BLOCK_SZ = "block_zs.dat"
BLOCK_FG = "block_fg.dat"
BLOCK_GN = "block_gn.dat"
BLOCK_DEFAULT = "block.dat"
TYPE_FLATS = 0
TYPE_GROUP = 1

# Each endpoint below returned and decoded realtime quotes, daily bars, and the
# complete 0x0452 special-price table during the 2026-07 capability audits.
# CandidateRegistry probes quote/bar capabilities again before exposing a
# latency-ranked snapshot.
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
    ("补充行情主站1", "182.140.139.191", 7709),
    ("补充行情主站2", "119.6.200.40", 7709),
    ("补充行情主站3", "218.200.222.134", 7709),
    ("补充行情主站4", "182.150.28.166", 7709),
)

# These supplemental quote nodes return the 0x054C mode-1 fund-flow extension.
# Generic quote nodes return an all-zero extension for the same request.
FUND_FLOW_HOSTS = frozenset(
    {
        ("182.140.139.191", 7709),
        ("119.6.200.40", 7709),
        ("218.200.222.134", 7709),
        ("182.150.28.166", 7709),
    }
)

CAPABILITY_FUND_FLOWS = "fund_flows"
CAPABILITY_MAC_A = "mac_a"
CAPABILITY_MAC_EX = "mac_ex"

MAC_HOSTS = (
    ("MAC行情主站1", "121.36.248.138", 7709),
    ("MAC行情主站2", "123.60.47.136", 7709),
    ("MAC行情主站3", "121.37.207.165", 7709),
)

MAC_EX_HOSTS = (
    ("MAC扩展行情主站1", "116.205.135.205", 7727),
    ("MAC扩展行情主站2", "121.37.232.167", 7727),
)

EX_HOSTS = (
    # Sourced from the TDX connect.cfg [DSHOST] ecosystem and independently
    # TCP-probed on 2026-07-30. CandidateRegistry performs an application-level
    # ExHq probe before choosing a latency-ranked process snapshot.
    ("扩展市场广州双线1", "116.205.143.214", 7727),
    ("扩展市场广州双线2", "124.71.223.19", 7727),
    ("扩展市场广州双线4", "113.45.175.47", 7727),
    ("扩展市场上海双线7", "175.24.47.69", 7727),
    ("扩展市场上海双线1", "150.158.9.199", 7727),
    ("扩展市场上海双线2", "150.158.20.127", 7727),
    ("扩展市场上海双线3", "49.235.119.116", 7727),
    ("扩展市场上海双线4", "49.234.13.160", 7727),
    ("扩展市场上海双线5", "123.60.173.210", 7727),
    ("扩展市场上海双线6", "118.89.69.202", 7727),
    ("扩展市场北京双线1", "112.74.214.43", 7727),
    ("扩展市场北京双线2", "120.25.218.6", 7727),
    ("扩展市场北京双线3", "43.139.173.246", 7727),
    ("扩展市场北京双线4", "159.75.90.107", 7727),
    ("扩展市场北京双线5", "106.52.170.195", 7727),
    ("扩展市场北京双线6", "139.9.191.175", 7727),
)

FINANCIAL_BASE_URL = "https://data.tdx.com.cn/tdxfin"
FINANCIAL_CATALOG_FILE = "gpcw.txt"
