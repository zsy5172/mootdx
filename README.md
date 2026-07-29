# mootdx

通达信标准市场行情与本地数据读取工具。

当前版本默认使用全新的 `next` 引擎：它不依赖 `tdxpy`，提供独立协议解析、连接池、节点故障转移、进程级候选 IP 缓存和线程安全的异步调用。

如果喜欢本项目，可以在右上角点一颗 ⭐。

> 本项目仅供学习与研究，不得用于任何商业用途。行情数据来自外部服务，项目不保证其完整性、实时性或持续可用性。

- 开源协议：MIT License
- 项目仓库：<https://github.com/mootdx/mootdx>
- 问题反馈：<https://github.com/mootdx/mootdx/issues>
- 国内镜像：<https://gitee.com/ibopo/mootdx>

## 主要能力

- `Quotes.factory()` 默认使用 `next`，现有标准市场调用通常无需修改。
- 支持实时行情、K 线、指数、分时、逐笔成交、财务、除权除息、F10 和板块数据。
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

next 兼容层的复权数据来自通达信日线和 `xdxr`，不依赖外部复权因子服务，并提供两组明确区分的语义：

- `qfq`、`hfq` 保留现有比例复权。普通股票按除权参考价生成比例因子；ETF、LOF、封闭基金和 REIT 使用可逆的扩缩股与现金分配仿射模型。
- `tdx_qfq`、`tdx_hfq` 对所有证券使用通达信桌面客户端的仿射复权公式。连续现金分红会形成价格平移，因此很早的 `tdx_qfq` 历史价格可能为负数；这是客户端原生结果，不是计算溢出。

所有模式下，`week`、`mon`、`3mon`、`year` 都会先逐日复权，再生成周期 OHLC，避免一个周期跨越除权日时开盘、最高和最低价失真。分钟线只调整 `price`，`vol`、`volume` 和 `amount` 保持原始成交口径。

证券代码会先去除首尾空白并统一市场前缀大小写，复权支持上海、深圳和北京市场。`xdxr` category 12（非流通股缩股）不会改变流通股价格。对于 category 13/14 权证派发，比例模式仍在请求区间依赖未知权证估值时抛出明确异常；`tdx_qfq`、`tdx_hfq` 则与实测桌面客户端一致，不把该记录纳入价格变换。600036 在 2006-02-27 的真实客户端抓包和 OHLC 基准已用于回归测试。

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
```

`transaction()` 与历史接口 `transactions()` 都会直接请求上游，不受运行机器的本地时钟限制；非交易时段
实时接口没有数据时返回空结果。
即时逐笔的单次 `offset` 范围为 1～1800，历史逐笔为 1～2000；更多数据请递增 `start` 分页读取。

### 财务、除权除息与 F10

```python
# 财务数据
client.finance("600036")

# 除权除息
client.xdxr("600036")

# F10 目录
categories = client.F10C("600036")

# 指定 F10 栏目
overview = client.F10("600036", name="公司概况")

# name 为空时返回全部可用栏目
all_f10 = client.F10("600036")
```

F10/F10C 示例使用 `600036`，避免 `000001` 在不同市场和服务节点上的语义差异。

### 板块数据

```python
client.block(tofile="block.dat")
client.block(tofile="block_zs.dat")
```

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
from mootdx_next import invalidate_hq_candidates
from mootdx_next import refresh_hq_candidates

refresh_hq_candidates()
invalidate_hq_candidates()
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

以下远程功能已经明确废弃：

- 在线扩展市场行情，即 `Quotes.factory(market="ext")`
- 旧 GP socket 财务下载线路

`Affair.files()` 和 `Affair.fetch()` 仍受支持，但已经改为通达信官方 HTTPS 财务服务；本地 `ExtReader`
也仍完整支持扩展市场文件。废弃的是在线 EX 行情和旧 GP socket，不是本地 Reader。

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

## 常见问题

### 为什么 `000001` 有时是股票，有时是指数？

`000001` 在深圳是平安银行，在上海是上证指数。普通股票接口会按代码规则推断为深圳；指数接口建议显式传入 `market=1`，或者使用带市场前缀的证券代码。

### 为什么实时逐笔成交在晚上不能测试？

`transaction()` 是交易时段接口。非交易时段请使用带历史日期的 `transactions()`，或者等待交易时间运行真实节点测试。

### `bestip=True` 会启动后台进程吗？

不会。候选注册表只是当前 Python 进程中的线程安全模块级对象，采用懒加载和 10 分钟有效期。
