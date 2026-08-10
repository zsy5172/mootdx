# 标准行情接口

下面是如何在程序里面调用本接口

**参数说明:**

- market: 对应市场。 (std 标准股票市场，ext 扩展市场)

** 调用方法：**

```python
from mootdx.quotes import Quotes

client = Quotes.factory(market='std')
```

### 其他参数

```python
from mootdx.quotes import Quotes

client = Quotes.factory(market='std', multithread=True, heartbeat=True, bestip=False, timeout=15)
# multithread 多线程
# heartbeat 开启心跳包
# bestip=True 触发进程内测速并缓存 10 分钟，不写旧版配置
# server 自行设置服务器IP, 格式 `server=('127.0.0.1', 7727)`
# timeout 设置超时时间
# quiet 日志静默方式, 默认False, 设置为 True 则不打印日志信息
# verbose 日志显示等级 0, 静默模式, 1 一般级别, 2 详细级别
```

## 01. 查询实时行情

可以获取**多**只股票的行情信息

**参数说明: **

- symbol: 多个股票号码。 `["000001", "600300"]` 格式

返回值：

- pd.DataFrame

**调用方法：**

```python
from mootdx.quotes import Quotes

client = Quotes.factory(market='std')
client.quotes(symbol=["000001", "600300"])

# 超过通达信单包上限时自动按每批 80 个证券查询
client.quotes_all(symbol=["sh600036", "sz300750", "bj920001"])
```

`quotes()` 保留原生单次请求语义；`quotes_all()` 只负责按协议上限分批并按批次拼接结果，不会按交易所、
板块或证券类型隐藏过滤调用者传入的代码。

### 全市场证券目录

```python
securities = client.securities()
stocks = securities.query("security_type_name == 'A股'")
star = securities.query("board_name == '科创板'")
chinext = securities.query("board_name == '创业板'")
bse = securities.query("exchange == 'BSE'")
```

`securities()` 返回沪、深、北标准市场的完整证券目录。协议市场代码和原有 `security_type` 保持兼容，
同时提供用于稳定筛选的 `exchange` / `board` 以及官方中文名称 `exchange_name` / `board_name` /
`security_type_name`。这些字段只描述证券的客观归属，不包含是否允许交易之类的用户策略。

| `exchange` | `exchange_name` | `board` | `board_name` |
| --- | --- | --- | --- |
| `SSE` | 上海证券交易所 | `main` | 主板 |
| `SSE` | 上海证券交易所 | `star` | 科创板 |
| `SZSE` | 深圳证券交易所 | `main` | 主板 |
| `SZSE` | 深圳证券交易所 | `chinext` | 创业板 |
| `BSE` | 北京证券交易所 | 空 | 空 |

北京证券交易所本身是交易所，不伪造一个“北交所板”名称。根据北交所《关于北交所存量上市公司代码
切换上线的通知》，自 2025 年 10 月 9 日起，当前上市公司股票的交易和行情查询统一使用
`920000`～`920999` 代码；旧代码只用于历史兼容。官方通知：
<https://www.bse.cn/important_news/200026735.html>。

### 交易阶段

实时行情结果中的 `trading_phase` 是行情服务器返回的交易阶段码，类型为整数。该值来自行情协议中的
状态位，不依赖运行 mootdx 的计算机本地时间。可使用 next 引擎公开的 `TRADING_PHASES` 常量翻译：

```python
from mootdx.quotes import Quotes
from mootdx_next import TRADING_PHASES

client = Quotes.factory(market='std')
quote = client.quotes(symbol='600036').iloc[0]

phase_code = int(quote['trading_phase'])
phase_name = TRADING_PHASES.get(phase_code, '未知状态')
```

`TRADING_PHASES` 的已知取值如下：

| 状态码 | 通达信客户端显示 |
| ---: | --- |
| 0 | 空 |
| 1 | 开盘前 |
| 2 | 开盘集合竞价 |
| 3 | 连续竞价 |
| 4 | 收盘集合竞价 |
| 5 | 闭市阶段 |
| 6 | 连续竞价闭市 |
| 7 | 盘后连续撮合 |
| 8 | 停牌 |
| 9 | 空 |
| 10 | 空 |
| 11 | 盘后停牌 |
| 12 | 波动中断 |
| 13 | 盘中休市 |
| 14 | 匹配临时停牌 |
| 15 | 保留/未知 |

其中 `TRADING_PHASES[0]`、`TRADING_PHASES[9]` 和 `TRADING_PHASES[10]` 均为空字符串；状态码 `15`
不在映射中。遇到未收录的状态码时，应保留数值并按未知状态处理。

### 涨跌停价格

next 引擎提供两个不同层级的涨跌停接口：

```python
from mootdx.quotes import Quotes

client = Quotes.factory(engine='next')

# 单个证券，高层接口返回一行 DataFrame
result = client.price_limit('600036')

# 服务器特殊价格表；start/count 是 0x0452 的分页参数
special = client.limit_prices(start=0, count=2000)

# 主动刷新进程缓存
fresh = client.price_limit('600053', refresh=True)
```

`limit_prices()` 直接读取通达信 `0x0452` 命令，单页 `count` 范围为 1～2000。其结果只是一张
“特殊证券涨跌停价格表”，不是全市场证券列表。2026-07-28 的真实客户端抓包确认，每条记录由
`market`、六位 `code`、`limit_up` 和 `limit_down` 组成，覆盖 ST、可转债、上市初期证券和其他
特殊限制品种。

`price_limit()` 的处理顺序如下：

1. 优先匹配服务器特殊价格表，`source='server'`；
2. 未命中时读取实时行情的前收价；
3. 只对规则明确的普通沪深 A 股、科创板、创业板和北交所股票计算，`source='calculated'`；
4. 基金、债券、B 股、无有效前收或无法确认规则的证券返回空结果，不根据名称猜测。

特殊价格表在当前 Python 进程内缓存 10 分钟，缓存快照不可变且线程安全，不会创建后台进程。可以使用
`refresh=True` 强制刷新，也可以调用 `mootdx_next.invalidate_price_limit_cache()` 使缓存失效。
Raw `SyncClient`/`AsyncClient` 的 `limit_prices()` 返回 `list[dict]`，`price_limit()` 返回
`dict | None`；Pandas 客户端和 `Quotes.factory(engine='next')` 返回 DataFrame。

## 02. 获取k线数据

**调用方法：**

> frequency -> K线种类
> 0 => 5分钟K线             => 5m
> 1 => 15分钟K线            => 15m
> 2 => 30分钟K线            => 30m
> 3 => 小时K线              => 1h
> 4 => 日K线 (小数点x100)    => days
> 5 => 周K线                => week
> 6 => 月K线                => mon
> 7 => 1分钟K线(好像一样)     => 1m
> 8 => 1分钟K线(好像一样)     => 1m
> 9 => 日K线                => day
> 10 => 季K线               => 3mon
> 11 => 年K线               => year

如

**调用方法：**

```python
from mootdx.quotes import Quotes

client = Quotes.factory(market='std')
client.bars(symbol='600036', frequency=9, offset=10)

# 前复权
client.bars(symbol='600036', adjust='qfq')

# 后复权
client.bars(symbol='600036', adjust='hfq')

# 通达信桌面客户端原生前复权 / 后复权
client.bars(symbol='600036', adjust='tdx_qfq')
client.bars(symbol='600036', adjust='tdx_hfq')
```

`qfq`、`hfq` 保留比例复权兼容语义；`tdx_qfq`、`tdx_hfq` 使用通达信客户端的仿射价格变换。
原生前复权连续扣除历史现金分红，因此很早的价格可能为负数。原生模式返回乘法 `factor` 和加法
`offset`，最终价格按照证券精度执行 `ROUND_HALF_UP`。两组模式都不调整成交量和成交额；已公告但
除权日晚于最新行情的事件不会提前生效。

`get_k_data`、`k` 和 `ohlc` 都是保留原版名字与签名的一等公共 API，仅在内部共享历史 K 线实现，
迁移时不需要调换方法名。

## 03. 查询股票数量

** 参数说明: **

- market: 市场代码. 0 - 深圳, 1 - 上海 (可以使用常量 `MARKET_SZ`, `MARKET_SH` 代替)

** 调用方法：**

```python
from mootdx.quotes import Quotes
from mootdx import consts

client = Quotes.factory(market='std')
client.stock_count(market=consts.MARKET_SH)
```

## 04. 查询股票列表

** 参数说明: **

- market: 市场代码. 0 - 深圳, 1 - 上海 (可以使用常量 `MARKET_SZ`, `MARKET_SH` 代替)

> 注意，在引入 consts 之后， （`from mootdx import consts`）
> 我们可以使用 consts.MARKET_SH , consts.MARKET_SZ 常量来代替 1 和 0 作为参数

** 调用方法：**

```python
from mootdx.quotes import Quotes
from mootdx import consts

client = Quotes.factory(market='std')
symbol = client.stocks(market=consts.MARKET_SH)
```

## 05. 指数K线行情

** 参数说明: **

- frequency: K线种类
- market: 市场代码. 0 - 深圳, 1 - 上海 (可以使用常量 `MARKET_SZ`, `MARKET_SH` 代替)
- start: 开始位置
- offset: 用户要请求的 K 线数目，最大值为 800

> frequency -> K线种类
> 0 => 5分钟K线             => 5m
> 1 => 15分钟K线            => 15m
> 2 => 30分钟K线            => 30m
> 3 => 小时K线              => 1h
> 4 => 日K线 (小数点x100)    => days
> 5 => 周K线                => week
> 6 => 月K线                => mon
> 7 => 1分钟K线(好像一样)     => 1m
> 8 => 1分钟K线(好像一样)     => 1m
> 9 => 日K线                => day
> 10 => 季K线               => 3mon
> 11 => 年K线               => year

使用说明：

** 调用方法：**

```python
from mootdx.quotes import Quotes
from mootdx.consts import MARKET_SH

client = Quotes.factory(market='std')
client.index(frequency=9, market=MARKET_SH, symbol='000001', start=1, offset=2)
```

## 06. 查询分时行情

> 网友反馈，此接口数据有误，不建议使用，可以使用 后面的 `历史分时行情` 来替代

** 参数说明: **

- symbol: 股票代码

** 调用方法：**

```python
from mootdx.quotes import Quotes

client = Quotes.factory(market='std')
client.minute(symbol='000001')
```

## 07. 历史分时行情

** 参数说明: **

- market: 市场代码.
- symbol: 股票代码
- date: 时间

** 调用方法：**

```python
from mootdx.quotes import Quotes

client = Quotes.factory(market='std')
client.minutes(symbol='000001', date='20171010')
```

注意，在引入 consts 之后， （`from mootdx import consts`） 我们可以使用 consts.MARKET_SH , consts.MARKET_SZ 常量来代替 1 和 0 作为参数

## 08. 查询分笔成交

** 参数说明: **

- market: 市场代码.
- start: 起始位置
- offset: 数量

** 调用方法：**

```python
from mootdx.quotes import Quotes

client = Quotes.factory(market='std')
client.transaction(symbol='600036', start=0, offset=10)
```

## 09. 查询历史分笔

** 参数说明: **

- symbol: 股票代码.
- start: 起始位置.
- offset: 数量.
- date: 日期.

** 调用方法：**

```python
from mootdx.quotes import Quotes

client = Quotes.factory(market='std')
client.transactions(symbol='000001', start=0, offset=10, date='20170209')
```

## 10. 公司信息目录

** 参数说明: **
市场代码， 股票代码， 如： 0,000001 或 1,600300

** 参数说明: **

- symbol: 股票代码.

** 调用方法：**

```python
from mootdx.quotes import Quotes

client = Quotes.factory(market='std')
client.F10C(symbol='000001')
```

## 11. 公司信息详情

** 参数说明: **

- symbol: 股票代码.
- name: 公司详情标题. 可使用`F10C`获取

**调用方法：**

```python
from mootdx.quotes import Quotes

client = Quotes.factory(market='std')
client.F10(symbol='000001', name='最新提示')
```

注意这里的 公司详情标题 参考上面接口的返回结果。

## 12. 除权除息信息

**参数说明: **

- symbol: 股票代码.

** 调用方法：**

```python
from mootdx.quotes import Quotes

client = Quotes.factory(market='std')
client.xdxr(symbol='600036')
```

## 13. 读取财务信息

**参数说明: **

- symbol: 股票代码.

**调用方法：**

```python
from mootdx.quotes import Quotes

client = Quotes.factory(market='std')
client.finance(symbol="600300")
```

## 14. 读取 OHLC k线信息

**参数说明: **

- symbol: 股票代码.
- begin: 开始时间.
- end: 结束时间.
- adjust: 复权模式，可选 `qfq`、`hfq`、`tdx_qfq`、`tdx_hfq`。

**调用方法：**

```python
from mootdx.quotes import Quotes

client = Quotes.factory(market='std')
client.k(symbol="600300", begin="2017-07-03", end="2017-07-10")

# 前复权
client.k(symbol="600300", begin="2017-07-03", end="2017-07-10", adjust='qfq')

# 后复权
client.k(symbol="600300", begin="2017-07-03", end="2017-07-10", adjust='hfq')

# 通达信客户端原生前复权 / 后复权
client.k(symbol="600300", begin="2017-07-03", end="2017-07-10", adjust='tdx_qfq')
client.k(symbol="600300", begin="2017-07-03", end="2017-07-10", adjust='tdx_hfq')

# ohlc 保留独立公开方法名，内部与 k 共享历史行情实现
client.ohlc(symbol="600300", begin="2017-07-03", end="2017-07-10")

# 前复权
client.ohlc(symbol="600300", begin="2017-07-03", end="2017-07-10", adjust='qfq')

# 后复权
client.ohlc(symbol="600300", begin="2017-07-03", end="2017-07-10", adjust='hfq')

# 通达信客户端原生前复权 / 后复权
client.ohlc(symbol="600300", begin="2017-07-03", end="2017-07-10", adjust='tdx_qfq')
client.ohlc(symbol="600300", begin="2017-07-03", end="2017-07-10", adjust='tdx_hfq')
```

## 15. 集合竞价

```python
from mootdx.quotes import Quotes

client = Quotes.factory(market='std')
client.call_auction(symbol='600036')
```

结果包含 `time`、`price`、`matched`、`unmatched`、`side` 和 `side_name`。`side` 为 `1` 表示买方未
匹配，为 `-1` 表示卖方未匹配，为 `0` 表示平衡。协议没有返回交易日期，因此不会用本机日期合成
`datetime`。

## 16. 公共报表、板块和盘后配置

```python
# 五类板块的统一目录（Pandas DataFrame）
catalog = client.block_catalog()
regions = client.block_catalog(category='地区')
sw_industries = client.block_catalog(category='industry').query("taxonomy == 'sw'")

# 查询成分；推荐使用 catalog 中的板块代码
beijing = client.block_members('880207')
concept_5g = client.block_members('880506')

# 一次取得扁平化的“板块—证券”关系
concept_members = client.block_members_all(category='概念')

# 证券和板块共用同一个资金流接口
funds = client.fund_flows(['600036', '880550', '880301'])

# 调用者自行组合目录、排序和派生指标
concepts = client.block_catalog(category='概念')
concept_funds = client.fund_flows(concepts['code'].tolist())
driver = concept_funds.sort_values('main_net_amount', ascending=False)
game = concept_funds.sort_values('main_net_amount_5min', ascending=False)

# 比率字段所用的板块股本和市值基准
base = client.tdx_block_base()

# 强制重新下载本次查询依赖的公共文件
beijing = client.block_members('880207', refresh=True)

# 通达信公共文件
client.block_file_raw('block_gn.dat')
client.report_file('zhb.zip')
client.zhb_files(refresh=False)

# 板块指数和附带指数 ID 的成分
client.tdx_block_indexes()
client.tdx_block_aliases()
client.block_with_index('block_gn.dat')

# 大型指数/专业板块、行业归属、新股申购和盘后统计
client.sp_blocks(name='中证2000')
client.tdx_industries()
client.ipo_subscriptions()
client.stock_statistics()
client.stock_statistics2()
```

`block_catalog(category=None, refresh=False)` 统一列出地区、行业、概念、风格和指数板块，主要字段为
`name`、`code`、`type`、`subtype`、`reference`、`category`、`category_name`、`taxonomy` 和
`source`。分类参数既接受英文 `region` / `industry` / `concept` / `style` / `index`，也接受中文
`地区` / `行业` / `概念` / `风格` / `指数` 及对应的“板块”全称。

`block_members(block, category=None, refresh=False)` 接受板块代码或名称。不同分类或行业体系可能有
同名板块；名称不能唯一匹配时会抛出 `ValueError`，应改用目录返回的板块代码。`Quotes.factory()` 的
next 引擎返回 `DataFrame`；若直接使用 `mootdx_next.SyncClient` 或 `AsyncClient`，对应方法返回
`list[dict]`。

`block_members_all(category=None, refresh=False)` 返回相同字段的扁平关系表，不查询行情、不筛选证券，
方便调用者自行与 `securities()`、`quotes_all()` 或历史快照连接。

| 分类 | 目录来源 | 成分来源 |
| --- | --- | --- |
| 地区（`type=3`） | `tdxzs3.cfg` | `base.dbf` 的 `DY` 地区字段 |
| 行业（`type=2`） | `tdxzs3.cfg` | `tdxhy.cfg` 的通达信行业字段 |
| 行业（申万，`type=12`） | `tdxzs3.cfg` | `tdxhy.cfg` 的申万行业字段 |
| 概念（`type=4`） | `tdxzs3.cfg` | `infoharbor_block.dat`，缺失时回退 `block_gn.dat` |
| 风格（`type=5`） | `tdxzs3.cfg` | `infoharbor_block.dat`，缺失时回退 `block_fg.dat` |
| 指数 | `infoharbor_block.dat` | `infoharbor_block.dat`，缺失时回退 `block_zs.dat` |

目录文件优先读取更完整的 `tdxzs3.cfg`，服务器没有该文件时自动回退 `tdxzs.cfg`。`refresh=True`
会忽略当前缓存并重新下载本次调用依赖的目录或成分文件。

### 自由组合板块行情

```python
from mootdx_next import aggregate_block_quotes

securities = client.securities()
symbols = securities.query("security_type_name == 'A股'")['symbol'].tolist()
quotes = client.quotes_all(symbols)
blocks = client.block_catalog(category='概念')
members = client.block_members_all(category='概念')

snapshot = aggregate_block_quotes(
    quotes=quotes,
    members=members,
    blocks=blocks,
)
leaders = snapshot.sort_values('amount', ascending=False)
```

`aggregate_block_quotes()` 是无网络访问的纯计算函数。它不会自行选择股票池、过滤交易所、计算综合分或
决定排序，只聚合调用者传入的行情和成分关系。结果提供成员数、行情覆盖率、成交量额、成交额加权涨幅、
涨跌家数、上涨占比及可用时的涨跌停家数。传入 09:25 冻结行情得到竞价板块快照；传入其他时点行情则
得到对应时点的板块快照。

### 证券与板块资金流

`fund_flows(symbol=None)` 直接查询通达信 `0x054C` mode 1 返回的行情和资金流扩展字段。
这个上游请求同时接受证券代码和板块代码，因此 mootdx 不再将它封装成板块专用 API。`symbol`
可以是一个代码或代码列表，接受 `sh600036` / `sz300750` / `bj920001` 这类市场前缀；不传或传
空列表时返回空结果。原生协议每批最多查询 80 个代码，客户端会自动分批。

`fund_flows()` 只返回上游数据：不隐式读取板块目录，不补全名称、分类或市值，也不预设股票池、
过滤规则和排序方式。如需查询某类板块，先用 `block_catalog()` 取得代码，再把代码列表传给
`fund_flows()`；如需按主力净额或 5 分钟主力净额排名，由调用者对返回结果排序。

资金流金额是上游服务器直接返回的结果，不是将 `block_members()` 的全部成分股逐只查询后求和。
`fund_flows` 只从支持资金扩展的补充行情节点中选择服务器。使用 `bestip=True` 时，健康的补充节点会
保留在测速快照中。若响应中的 `fund_amount_base` 为零，客户端会切换到下一个补充节点；节点不可用或达到
配置的重试次数时明确报错，不回退到只会返回全零资金扩展的普通行情节点。

如需按板块流通市值计算比率，调用者可单独取得 `tdx_block_base()` 并按 `market` 和 `code` 连接：

- `net_buy_rate = main_buy_amount / circulating_market_cap * 100`
- `main_force_net_ratio = main_net_amount / circulating_market_cap * 100`

这两个派生字段不由 `fund_flows()` 返回。

成交额占比直接使用同一行情包中的板块成交额：

- `main_buy_share = main_buy_amount / amount * 100`
- `main_force_share = main_net_amount / amount * 100`
- `main_force_share_5min = main_net_amount_5min / amount * 100`

金额字段单位统一为元，比率字段统一为百分点。主要字段如下：

| 页面 | 通达信含义 | 返回字段 |
| --- | --- | --- |
| 资金驱动力 | 主力净额、主力占比 | `main_net_amount`、`main_force_share` |
| 资金驱动力 | 主买净额、主买占比 | `main_buy_amount`、`main_buy_share` |
| 资金驱动力 | 量比、短换、2 分钟金额 | `volume_growth_rate`、`short_turnover_rate`、`amount_2min` |
| 资金博弈 | 当日超大单、大单、中单、小单净额 | `super_large_net_amount`、`large_net_amount`、`medium_net_amount`、`small_net_amount` |
| 资金博弈 | 5 分钟主力净额、主力占比 | `main_net_amount_5min`、`main_force_share_5min` |
| 资金博弈 | 5 分钟超大单、大单、中单、小单净额 | `super_large_net_amount_5min`、`large_net_amount_5min`、`medium_net_amount_5min`、`small_net_amount_5min` |
| 资金博弈 | 散户单增长比 | `retail_order_growth_ratio` |

`tdx_block_base(refresh=False)` 可单独取得股本、市值和基准日期。Raw 同步/异步客户端返回 `list[dict]`；
Pandas 客户端和 `Quotes.factory(engine='next')` 返回 `DataFrame`。

`zhb.zip` 只在内存中安全解压，使用进程级线程安全快照缓存 10 分钟。`refresh=True` 可主动刷新。
`stock_statistics()` 和 `stock_statistics2()` 是服务器发布的盘后快照；尚未通过客户端界面验证语义的
列保留在 `raw_fields`，不会用推测名称冒充已知资金指标。
