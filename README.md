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

# 5 分钟 K 线
client.bars("600036", frequency="5m", start=0, offset=100)

# 上证指数；000001 同时也是深市股票代码，因此建议明确 market=1
client.index("000001", market=1, frequency="day", offset=100)

# 指定日期区间，自动从最新一页向历史分页
client.get_k_data(
    code="600036",
    start_date="2019-07-03",
    end_date="2019-07-10",
)

# 兼容别名
client.k(symbol="600036", begin="2019-07-03", end="2019-07-10")
client.ohlc(symbol="600036", begin="2019-07-03", end="2019-07-10")
```

常用频率别名包括：`5m`、`15m`、`30m`、`1h`、`1m`、`day`、`week`、`mon`、`3mon`、`year`。底层同时接受 `0` 到 `11` 的通达信频率编号。

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

`transaction()` 在非交易时段会抛出明确的交易时段异常；历史接口 `transactions()` 不受当前交易时间限制。
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

兼容层适合原有 mootdx 用户；如果希望直接得到 Python `list`/`dict`，可以使用 `mootdx_next`。

### 同步客户端

```python
from mootdx_next import SyncClient

client = SyncClient(max_retries=1)

try:
    quotes = client.quotes(["600036", "000001"])
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

        index = await client.index_bars("000001", market=1, frequency="day", offset=10)
        block = await client.block("block_zs.dat")
        categories = await client.f10_categories("600036")
        overview = await client.f10_content("600036", "公司概况")
    finally:
        client.close()


asyncio.run(main())
```

`AsyncClient` 与 `SyncClient` 的业务方法保持一致。客户端查找和实际调用都在线程池工作线程内完成，不会在线程之间共享非线程安全的连接池状态。

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
- GP 财务文件列表与远程下载，即 `Affair.files()`、`Affair.fetch()`

已经下载到本地的通达信财务文件仍可使用本地解析能力。

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
