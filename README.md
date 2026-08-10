# mootdx

通达信标准市场、扩展市场行情与本地数据读取工具。

当前版本默认使用全新的 `next` 引擎：它不依赖 `tdxpy`，提供独立协议解析、连接池、节点故障转移、进程级候选 IP 缓存和线程安全的异步调用。

如果喜欢本项目，可以在右上角点一颗 ⭐。

> 本项目仅供学习与研究，不得用于任何商业用途。行情数据来自外部服务，项目不保证其完整性、实时性或持续可用性。

- 开源协议：MIT License
- 项目仓库：<https://github.com/mootdx/mootdx>
- 问题反馈：<https://github.com/mootdx/mootdx/issues>
- 国内镜像：<https://gitee.com/ibopo/mootdx>

## 主要能力

- `Quotes.factory()` 默认使用 `next`，现有标准市场调用通常无需修改。
- 支持实时行情、K 线、指数、集合竞价、分时、逐笔成交、财务、除权除息、F10、板块和公共配置数据。
- 原生支持 ExHq 扩展行情，包括期货、港股、外盘的市场表、品种、报价、K 线、分时和逐笔。
- 支持同步 `SyncClient` 和线程隔离的 `AsyncClient`。
- 每个异步工作线程使用独立客户端和连接池，共享不可变的候选 IP 快照。
- `bestip=True` 触发进程内测速，结果缓存 10 分钟，不再写入旧版 `BESTIP` 配置。
- 内置通达信日线、分钟线、板块和自定义板块 Reader，不需要 legacy 依赖。
- 提供协议语料回放、完整参数矩阵和可选真实节点测试。

## 运行环境

- Windows、macOS、Linux
- Python 3.11、3.12、3.13、3.14

## 安装

默认安装已经包含 `next` 引擎所需的全部运行依赖：

```bash
pip install -U mootdx
```

只有仍需显式使用旧引擎时，才安装可选的 legacy 依赖：

```bash
pip install -U 'mootdx[legacy]'
```

## 快速开始

`Quotes.factory()` 默认等同于 `Quotes.factory(engine="next")`：

```python
from mootdx.quotes import Quotes

client = Quotes.factory()

try:
    quote = client.quotes("600036")
    bars = client.bars("600036", frequency="day", offset=10)
    index = client.index("000001", market=1, frequency="day", offset=10)
finally:
    client.close()
```

兼容层中的行情、K 线等表格型接口返回 Pandas `DataFrame`；如果没有数据，则返回空 `DataFrame`。计数和 F10 等接口保留各自的整数、列表、文本或字典返回类型。

## 标准市场接口

### 行情与证券列表

```python
# 单只证券
client.quotes("600036")

# 批量证券，也支持明确市场前缀
client.quotes(["600036", "sz000001", "bj430090"])

# 市场证券数量：0=深圳，1=上海，2=北京
client.stock_count(1)

# 上海或深圳证券列表
client.stocks(1)
client.stock_all()

# 按真实证券类型筛选；返回带市场前缀的代码
client.stock_codes()
client.etf_codes()
client.index_codes()

# 北交所目录由公开行情快照补充，market=2
client.stocks(2)
```

### K 线与指数

```python
# 日 K 线
client.bars("600036", frequency="day", start=0, offset=100)

# 兼容原有比例复权语义
client.bars("600036", frequency="day", offset=100, adjust="qfq")
client.bars("600036", frequency="day", offset=100, adjust="hfq")

# 通达信客户端原生前复权 / 后复权语义
client.bars("600036", frequency="day", offset=100, adjust="tdx_qfq")
client.bars("600036", frequency="day", offset=100, adjust="tdx_hfq")

# ETF 复权
client.bars("510500", frequency="day", offset=100, adjust="qfq")

# 周、月、季、年 K 线会先复权日线，再聚合 OHLC
client.bars("600036", frequency="week", offset=100, adjust="qfq")

# 5 分钟 K 线
client.bars("600036", frequency="5m", start=0, offset=100)

# 上证指数；000001 同时也是深市股票代码，因此建议明确 market=1
client.index("000001", market=1, frequency="day", offset=100)

# next 原生指数接口，以及自动分页的全量/条件读取
client.index_bars("000001", market=1, frequency="day", offset=100)
client.index_bars_all("000001", market=1, frequency="day")
client.index_bars_until(
    "000001",
    lambda row: row["datetime"] <= "2020-01-01 15:00",
    market=1,
)

# 普通证券也提供同样的分页能力
client.bars_all("600036", frequency="day")
client.bars_until(
    "600036",
    lambda row: row["datetime"] <= "2020-01-01 15:00",
    frequency="day",
)

# 指定日期区间，自动从最新一页向历史分页
client.get_k_data(
    code="600036",
    start_date="2019-07-03",
    end_date="2019-07-10",
    adjust="qfq",
)

# 保留原签名的一等 API
client.k(symbol="600036", begin="2019-07-03", end="2019-07-10", adjust="qfq")
client.ohlc(symbol="600036", begin="2019-07-03", end="2019-07-10", adjust="hfq")
```

常用频率别名包括：`5m`、`15m`、`30m`、`1h`、`1m`、`day`、`week`、`mon`、`3mon`、`year`。底层同时接受 `0` 到 `11` 的通达信频率编号。

`get_k_data(code, start_date, end_date, adjust=None)`、`k(symbol="", begin=None, end=None, **kwargs)` 和
`ohlc(**kwargs)` 分别保留原版公开名字与签名，不是互相替换的迁移别名。它们只在内部共享历史 K 线分页实现，下游代码无需调换方法名。

标准证券 K 线的 `vol`、`volume` 在各周期统一为“手”，`amount` 为元。指数协议有一个需要特别区分的
上游口径：日线及更长周期的 `volume` 是手，但分钟包的同一槽位实际约等于 `amount / 100`，并非可与
日线相加的成交量。next 原生 `index_bars*()` 因此同时返回：

- `volume_unit="lot"`、`volume_lots=<手数>`：日线及更长周期；
- `volume_unit="hundred_yuan_turnover"`、`volume_lots=None`、`turnover_100_yuan=<百元成交额>`：分钟周期；
- `volume_raw`：未做单位换算的协议值。

为保持旧调用结果，兼容入口 `index()` 继续让 `vol`、`volume` 使用原版协议值；需要明确单位的新代码应
使用 `index_bars()` 及上述字段。该差异已用上证指数和深证成指多个完整交易日的分钟、日线真实包核验。

next 兼容层的复权数据来自通达信日线和 `xdxr`，不依赖外部复权因子服务，并提供两组明确区分的语义：

- `qfq`、`hfq` 保留现有比例复权。普通股票按除权参考价生成比例因子；ETF、LOF、封闭基金和 REIT 使用可逆的扩缩股与现金分配仿射模型。
- `tdx_qfq`、`tdx_hfq` 对所有证券使用通达信桌面客户端的仿射复权公式。连续现金分红会形成价格平移，因此很早的 `tdx_qfq` 历史价格可能为负数；这是客户端原生结果，不是计算溢出。结果同时提供乘法 `factor` 和加法 `offset`，价格按照证券精度执行 `ROUND_HALF_UP`。

所有模式下，`week`、`mon`、`3mon`、`year` 都会先逐日复权，再生成周期 OHLC，避免一个周期跨越除权日时开盘、最高和最低价失真。分钟线只调整 `price`，`vol`、`volume` 和 `amount` 保持原始成交口径。

证券代码会先去除首尾空白并统一市场前缀大小写，复权支持上海、深圳和北京市场。`xdxr` category 12（非流通股缩股）不会改变流通股价格。对于 category 13/14 权证派发，比例模式仍在请求区间依赖未知权证估值时抛出明确异常；`tdx_qfq`、`tdx_hfq` 则与实测桌面客户端一致，不把该记录纳入价格变换。600036 在 2006-02-27 的真实客户端抓包和 OHLC 基准已用于回归测试。

停牌区间内的多次公司行为会按事件日期全部复合；`xdxr` 中除权日晚于最新可用交易日的已公告事件不会提前改变前复权锚点。事件边界与舍入口径也参考了 MIT 许可的 [injoyai/tdx 仿射复权实现](https://github.com/injoyai/tdx/commit/026e64a8abe804e987e455e8aaff3638c3dd7834)，并以本项目的服务端行情、基金扩缩股和同步/异步接口重新实现及验证。

### 涨跌停价格

```python
# 单个证券：返回一行 DataFrame
limit = client.price_limit("600036")

# 通达信服务器的特殊涨跌停价格表
special_limits = client.limit_prices(start=0, count=2000)

# 跳过进程缓存并主动刷新特殊价格表
limit = client.price_limit("600053", refresh=True)
```

next 引擎优先使用通达信 `0x0452` 返回的服务器特殊价格表。该表不是全市场列表，主要包含 ST、
可转债、上市初期证券等需要服务器明确给价的品种。未命中时，仅对规则明确的普通沪深 A 股、科创板、
创业板和北交所股票按照前收价计算；基金、债券、B 股及无法确认规则的证券不会被猜测。

`source` 列为 `server` 表示直接采用服务器值，为 `calculated` 表示普通股票规则计算。特殊价格表使用
进程级线程安全缓存，默认有效期 10 分钟；各客户端仍保有独立连接池。

### 分时与逐笔成交

```python
# 指定历史日期的分钟数据
client.minutes("600036", date="2017-10-10")

# 当日分钟数据
client.minute("600036")

# 历史逐笔成交，任何时间均可查询
client.transactions("600036", date="20170209", start=0, offset=100)

# 实时逐笔成交，仅在交易时段可用
client.transaction("600036", start=0, offset=100)

# 自动分页读取当天或指定历史交易日的全部逐笔
client.transaction_all("600036")
client.transactions_day("600036", date="20260729")

# 按交易日惰性读取日期区间；每次只在内存中保留一天
for trading_date, rows in client.iter_transactions(
    "600036",
    start_date="20260701",
    end_date="20260729",
):
    process(trading_date, rows)

# 自动从最早月 K 推断上市月份，惰性遍历完整逐笔历史
for trading_date, rows in client.iter_transaction_history("600036", before="20260729"):
    process(trading_date, rows)

# 交易日历来自上证指数日线的进程级不可变快照
client.trading_days("20260701", "20260729")
client.is_trading_day("20260729")

# 用 09:25 逐笔重建独立的 09:30 集合竞价 K，形成每天 241 根分钟线
client.minute_bars_241("600036", offset=800)
client.minute_bars_241_all("600036")
```

`transaction()` 与历史接口 `transactions()` 都会直接请求上游，不受运行机器的本地时钟限制；非交易时段
实时接口没有数据时返回空结果。
即时逐笔的单次 `offset` 范围为 1～1800，历史逐笔为 1～2000；更多数据请递增 `start` 分页读取。

集合竞价接口直接返回服务器的竞价序列：

```python
auction = client.call_auction("600036")
```

结果包含 `time`、`price`、`matched`、`unmatched`、`side` 和 `side_name`。未匹配量在协议中是带符号
32 位整数：正值为买方、负值为卖方，API 将数量取绝对值并把方向单独放在 `side`。服务器不返回交易
日期，因此接口不会根据本机日期猜测。

### 财务、除权除息与 F10

```python
# 财务数据
client.finance("600036")

# 除权除息
client.xdxr("600036")

# 按日期保留全部公司行为；同日事件不会互相覆盖
client.xdxr_by_date("600036")

# 指定日期的流通/总股本、市值与换手率
client.equity_at("600036", as_of="20260729")
client.market_value("600036", as_of="20260729", price=39.66)
client.turnover("600036", as_of="20260729", volume=503975, volume_unit="lots")

# F10 目录
categories = client.F10C("600036")

# 指定 F10 栏目
overview = client.F10("600036", name="公司概况")

# name 为空时返回全部可用栏目
all_f10 = client.F10("600036")
```

F10/F10C 示例使用 `600036`，避免 `000001` 在不同市场和服务节点上的语义差异。

`xdxr()` 保留响应内的 `market`、`code`、完整 `datetime`、`raw_c1`～`raw_c4`。股本字段同时提供
`*_wan_shares`（万股）和 `*_shares`（股），市值固定为“元”，避免换手率和市值计算再依赖隐含单位。

### 聚合、指标与未来收益率

分析函数独立于网络客户端，可直接处理 next 返回的记录或 Pandas 数据：

```python
from mootdx_next import aggregate_bars
from mootdx_next import atr, boll, ema, forward_returns, ma, macd, rsi, vwap
from mootdx_next import summarize_trade_sides, trades_to_minute_bars

daily = client.bars("600036", frequency="day", offset=160)
minute = client.bars("600036", frequency="1m", offset=800)
trades = client.transactions_day("600036", date="20260729")

ma5 = ma(daily, 5)
macd_frame = macd(daily)
returns = forward_returns(daily, horizons=(1, 5, 20))
five_minute = aggregate_bars(minute, "5min")
trade_summary = summarize_trade_sides(trades)
rebuilt = trades_to_minute_bars(trades, date="20260729", outside_session="drop")
```

指标返回与输入完整对齐的时间序列；MACD 使用通达信双倍柱值，BOLL 使用总体标准差，RSI/ATR 使用
Wilder 平滑。`vwap()` 默认金额单位为元、成交量单位为手。聚合函数会校验成交量和成交额守恒，并且
不会让分钟桶跨越午休或交易日。

### 板块数据

```python
# 统一目录：地区、行业、概念、风格、指数
catalog = client.block_catalog()
regions = client.block_catalog("地区")

# 建议用目录中的板块代码查询，避免同名板块歧义
beijing = client.block_members("880207")
concept_5g = client.block_members("880506")

# 证券和板块共用同一个资金流接口；返回值为 DataFrame
funds = client.fund_flows(["600036", "880550", "880301"])

# 调用者自行组合板块目录并排序
concepts = client.block_catalog("概念")
concept_funds = client.fund_flows(concepts["code"].tolist())
driver = concept_funds.sort_values("main_net_amount", ascending=False)
game = concept_funds.sort_values("main_net_amount_5min", ascending=False)

# 资金比率使用的板块股本、市值基准
base = client.tdx_block_base()

# 忽略当前客户端缓存，重新下载相关公共文件
fresh_beijing = client.block_members("880207", refresh=True)

# 仍可访问底层板块文件
client.block(tofile="block.dat")
client.block(tofile="block_zs.dat")

# 原始公共文件和 zhb.zip 内存快照
raw = client.block_file_raw("block_gn.dat")
archive = client.report_file("zhb.zip")
files = client.zhb_files()

# 板块指数、别名和附带指数 ID 的板块成分
client.tdx_block_indexes()
client.tdx_block_aliases()
client.block_with_index("block_gn.dat")

# 大型指数/专业板块、行业、新股申购和盘后统计快照
client.sp_blocks("中证2000")
client.tdx_industries()
client.ipo_subscriptions()
client.stock_statistics()
client.stock_statistics2()
```

`Quotes.factory(engine="next")` 返回的 Pandas 客户端会让 `block_catalog()` 和 `block_members()` 返回
`DataFrame`。`category` 可传 `region`、`industry`、`concept`、`style`、`index`，也可传
`地区`、`行业`、`概念`、`风格`、`指数`（或对应的“板块”全称）。目录优先读取 `tdxzs3.cfg`，
若服务器未提供则回退到 `tdxzs.cfg`；其中行业包含通达信 `type=2` 和申万 `type=12`，可通过
`taxonomy` 列区分 `tdx` / `sw`。

成分来源按类别选择：地区使用 `base.dbf` 的 `DY` 字段；行业使用 `tdxhy.cfg`；概念、风格和指数
优先使用完整的 `infoharbor_block.dat`，缺失时回退到 `block_gn.dat`、`block_fg.dat`、
`block_zs.dat`。同名板块可能同时存在于不同分类或行业体系中，名称无法唯一定位时会抛出
`ValueError`，此时应改用 `block_catalog()` 返回的板块代码。

`fund_flows(symbol=None)` 直接返回通达信 `0x054C` mode 1 的行情和资金流扩展字段。
上游请求同时支持证券代码和板块代码，每批最多 80 个代码，客户端会自动分批。它不隐式读取
板块目录或 `tdxzsbase.cfg`，不预设股票池、过滤和排序规则。主力净额、主买净额、当日四档资金流和
5 分钟四档资金流都是服务端数据，不是下载全部成分股后在本地合计。

该命令只路由到具有 `fund_flows` capability 的补充行情节点。内置已验证节点会自动声明该能力，自定义
节点可通过 `ServerEndpoint(capabilities=frozenset({'fund_flows'}))` 显式声明，普通行情节点不会成为
隐式回退。`fund_amount_base=0` 表示本次资金扩展尚不可用，客户端会原样返回零值并标记
`fund_extension_available=False`、`fund_extension_status='unavailable'`，不会误判节点故障或切换节点。
每行同时标记 `quote_source='tdx_0x054c_mode1'`。网络、解码和 capability 路由失败仍会明确报错。

如需主力净比或净买率，可单独调用 `tdx_block_base()`，按 `market` 和 `code` 与资金流结果连接后计算：

- `main_force_net_ratio = main_net_amount / circulating_market_cap * 100`
- `net_buy_rate = main_buy_amount / circulating_market_cap * 100`

这两个派生字段不由 `fund_flows()` 返回。所有 `*_amount` 字段单位为元，`*_share` 和已解析的
`*_ratio` / `*_rate` 字段均为百分点。主要映射如下：

| 通达信列 | 返回字段 |
| --- | --- |
| 主力净额 / 主力占比 | `main_net_amount` / `main_force_share` |
| 主买净额 / 主买占比 | `main_buy_amount` / `main_buy_share` |
| 超大单 / 大单 / 中单 / 小单净额 | `super_large_net_amount` / `large_net_amount` / `medium_net_amount` / `small_net_amount` |
| 5 分钟主力净额 / 主力占比 | `main_net_amount_5min` / `main_force_share_5min` |
| 5 分钟四档净额 | `super_large_net_amount_5min` / `large_net_amount_5min` / `medium_net_amount_5min` / `small_net_amount_5min` |
| 量比 / 短换 / 2 分钟金额 / 散户单增长比 | `volume_growth_rate` / `short_turnover_rate` / `amount_2min` / `retail_order_growth_ratio` |

`zhb.zip` 在内存中安全解压，并使用进程级线程安全快照缓存 10 分钟；主动刷新可传 `refresh=True`。
解压器拒绝路径穿越、重复成员、加密成员和超出限制的压缩包。`stock_statistics*()` 是服务器发布的盘后
快照，尚未验证语义的资金字段保留在不可变 `raw_fields` 中，不擅自命名。

## 扩展市场接口

默认 next 引擎已经恢复原生 ExHq，旧调用入口无需更换：

```python
from mootdx.quotes import Quotes

ex = Quotes.factory(market="ext", bestip=True)
try:
    markets = ex.markets()
    count = ex.instrument_count()
    instruments = ex.instrument(start=0, offset=100)
    quote = ex.quote(symbol="31#00700")
    bars = ex.bars(symbol="31#00700", frequency="day", offset=100)
    minute = ex.minute(symbol="31#00700")
    history_minute = ex.minutes(symbol="31#00700", date="20260729")
    trades = ex.transaction(symbol="31#00700", offset=100)
    history_trades = ex.transactions(symbol="31#00700", date="20260729", offset=100)
finally:
    ex.close()
```

`market` 可以单独传入，也可以使用 `market#symbol`，两者同时给出时必须一致。真实节点确认的单次上限
为：品种页 1000、批量报价 100、K 线 700、当前/历史逐笔 1800；超出上限会明确报错，因为服务器会
静默截断。`instrument_count()` 是服务端槽位计数，当前可能包含少量不可枚举的保留尾部，
`instruments()` 遇到合法全零空页会停止。

ExHq 当前逐笔没有交易日期，历史逐笔包含请求日期。协议原始逐笔价格是实际价格的 1000 倍，新引擎
返回修正后的 `price` 并保留 `price_raw`。当前分时/逐笔在对应市场非交易时段允许返回空结果。
`bars_range()` 是通达信 `0x240D` 日期区间接口，真实响应为区间分钟 K，并受服务端单次记录数限制。
完整字段和 Raw/Async 用法见 [扩展行情接口](docs/api/quote2.md)。

## 服务器选择

默认情况下，next 引擎使用内置的可用节点池，并在请求失败时切换节点。

### 进程内测速

```python
from mootdx.quotes import Quotes

client = Quotes.factory(bestip=True)
```

`bestip=True` 会在当前 Python 进程中测速一次并缓存候选结果 10 分钟。每个客户端都有独立连接池，但共享候选 IP 快照；它不会创建额外常驻进程，也不会修改旧版配置文件。

可以主动刷新或失效缓存：

```python
from mootdx_next import invalidate_ex_candidates
from mootdx_next import invalidate_hq_candidates
from mootdx_next import refresh_ex_candidates
from mootdx_next import refresh_hq_candidates

refresh_hq_candidates()
invalidate_hq_candidates()
refresh_ex_candidates()
invalidate_ex_candidates()
```

### 指定固定服务器

```python
client = Quotes.factory(server=("110.41.147.114", 7709))
```

显式 `server` 的优先级高于 `bestip=True`。

## 直接使用 next API

兼容层适合原有 mootdx 用户。`mootdx_next` 本身也是完整 SDK：生产代码不会导入 `mootdx`，可以直接选择返回
`list`/`dict` 的 Raw 客户端，或者返回 `DataFrame` 的 Pandas 客户端。两个包仍由同一个 `mootdx` wheel 发布，
依赖方向只允许旧入口单向调用 next。

### Pandas 客户端

```python
from mootdx_next import PandasClient

client = PandasClient(bestip=True)
try:
    bars = client.bars("600036", frequency="day", offset=100, adjust="qfq")
    history = client.get_k_data("600036", "2019-07-03", "2019-07-10")
finally:
    client.close()
```

### 同步客户端

```python
from mootdx_next import SyncClient

client = SyncClient(max_retries=1)

try:
    quotes = client.quotes(["600036", "000001"])
    limit = client.price_limit("600036")
    bars = client.bars("600036", frequency="day", offset=10)
    categories = client.f10_categories("600036")
finally:
    client.close()
```

### 异步客户端

```python
import asyncio

from mootdx_next import AsyncClient


async def main():
    client = AsyncClient(max_retries=1)
    try:
        quotes, bars = await asyncio.gather(
            client.quotes(["600036", "000001"]),
            client.bars("600036", frequency="day", offset=10),
        )

        limit = await client.price_limit("600036")
        index = await client.index_bars("000001", market=1, frequency="day", offset=10)
        block = await client.block("block_zs.dat")
        categories = await client.f10_categories("600036")
        overview = await client.f10_content("600036", "公司概况")
    finally:
        client.close()


asyncio.run(main())
```

`AsyncClient` 与 `SyncClient` 的业务方法保持一致，`AsyncPandasClient` 与 `PandasClient` 同样保持业务方法对称。
异步客户端查找和实际调用都在线程池工作线程内完成，不会在线程之间共享非线程安全的连接池状态。

### 扩展市场 SDK

```python
import asyncio

from mootdx_next import AsyncExClient
from mootdx_next import ExPandasClient
from mootdx_next import ExSyncClient

raw = ExSyncClient()
try:
    quote = raw.quote(31, "00700")
    bars = raw.bars(31, "00700", frequency="day", offset=100)
finally:
    raw.close()

pandas_client = ExPandasClient(bestip=True)
frame = pandas_client.quote(31, "00700")
pandas_client.close()


async def ex_main():
    client = AsyncExClient()
    try:
        return await asyncio.gather(
            client.quote(31, "00700"),
            client.bars(31, "00700", frequency="day", offset=10),
        )
    finally:
        client.close()
```

`AsyncExClient`/`ExSyncClient` 和 `AsyncExPandasClient`/`ExPandasClient` 分别保持完整方法对称。

### 财务文件 SDK

```python
from mootdx_next import FinancialFileClient
from mootdx_next import FinancialReader

with FinancialFileClient() as client:
    files = client.files()
    path = client.fetch("output", filename=files[-1]["filename"])

frame = FinancialReader.read(path)
```

财务目录和文件默认从通达信官方 HTTPS 地址读取，并校验目录中的文件大小与 MD5。`AsyncFinancialFileClient`
提供对称的 `catalog`、`files`、`fetch`、`parse` 和 `fetch_and_parse` 方法。

## 本地通达信文件

```python
from mootdx.reader import Reader

reader = Reader.factory(market="std", tdxdir="C:/new_tdx")

# 日线
reader.daily("600036")

# 1 分钟和 5 分钟数据
reader.minute("600036", suffix="1")
reader.minute("600036", suffix="5")

# 板块文件
reader.block("block_gn.dat")
```

本地 Reader 能安全读取空分钟文件，并返回列结构稳定的空 `DataFrame`。

## Legacy 与已废弃功能

旧引擎仍保留为临时迁移入口：

```python
client = Quotes.factory(engine="legacy")
```

它要求安装 `mootdx[legacy]`。新代码不建议依赖底层 `client.client` 的 tdxpy 私有方法。

旧 GP socket 财务下载线路已经明确废弃。

`Affair.files()` 和 `Affair.fetch()` 仍受支持，但已经改为通达信官方 HTTPS 财务服务；本地 `ExtReader`
也仍完整支持扩展市场文件。在线 EX 行情由 next 引擎的原生 ExHq 实现提供。

## 开发与测试

安装开发依赖：

```bash
uv sync --all-extras
```

运行默认测试：

```bash
uv run pytest
```

运行 next 全接口与参数矩阵：

```bash
uv run nox -s next_matrix
```

运行需要真实行情服务的完整矩阵：

```bash
uv run nox -s next_live_matrix
```

真实逐笔成交测试在非交易时段会明确跳过。测试设计说明参见 [Next engine test matrix](docs/next-test-matrix.md)。

扩展行情真实矩阵只在本地按需运行，不进入 GitHub Actions：

```bash
MOOTDX_NEXT_EX_LIVE=1 pytest tests/core_engine/test_next_ex_live_smoke.py -q
```

## 常见问题

### 为什么 `000001` 有时是股票，有时是指数？

`000001` 在深圳是平安银行，在上海是上证指数。普通股票接口会按代码规则推断为深圳；指数接口建议显式传入 `market=1`，或者使用带市场前缀的证券代码。

### 为什么实时逐笔成交在晚上不能测试？

`transaction()` 是交易时段接口。非交易时段请使用带历史日期的 `transactions()`，或者等待交易时间运行真实节点测试。

### `bestip=True` 会启动后台进程吗？

不会。候选注册表只是当前 Python 进程中的线程安全模块级对象，采用懒加载和 10 分钟有效期。
