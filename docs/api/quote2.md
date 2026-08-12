# 扩展行情接口

扩展行情使用通达信独立的 ExHq 服务（通常为 7727 端口），覆盖期货、期权、港股、外盘、外汇和全球
指数。`Quotes.factory(market='ext')` 默认调用不依赖 `tdxpy` 的 next 实现；只有显式指定
`engine='legacy'` 才走旧实现。

```python
from mootdx.quotes import Quotes

client = Quotes.factory(market='ext', bestip=True)
```

`bestip=True` 会执行 ExHq 应用层探测，而不只是测试 TCP 端口。节点必须能够解析市场表、品种数量和
港股日 K 才会进入候选快照。快照保存在当前 Python 进程中，线程安全，有效期 10 分钟，不会启动
常驻进程，也不会写旧版 `BESTIP` 配置。

所有需要品种的接口都接受两种等价写法：

```python
client.quote(market=31, symbol='00700')
client.quote(symbol='31#00700')
```

如果同时给出 `market` 和带前缀的 `symbol`，两者必须一致。代码最多 9 个 ASCII 字节。

## 01. 市场和品种

```python
# 市场表：market、category、name、short_name
markets = client.markets()

# 服务端品种槽位计数
count = client.instrument_count()

# 分页品种表
page = client.instrument(start=0, offset=100)

# 自动分页读取全部可枚举品种
all_instruments = client.instruments(page_size=1000)
```

品种页字段为 `start`、`category`、`market`、`code`、`name`、`description`。真实服务端当前允许单页
最多请求 1000 条。`instrument_count()` 是槽位计数，可能包含少量保留尾部，因此可能略大于
`len(instruments())`；尾部合法响应是 `count=0` 加一个 64 字节全零占位，新引擎会明确识别为空页。

## 02. 单品种五档行情

```python
quote = client.quote(symbol='31#00700')
```

主要字段包括：

- `market`、`code`
- `pre_close`、`open`、`high`、`low`、`price`
- `open_interest`、`volume`、`current_volume`、`inner_volume`、`outer_volume`、`position`
- `bid1`～`bid5`、`bid_vol1`～`bid_vol5`
- `ask1`～`ask5`、`ask_vol1`～`ask_vol5`

字段在不同品类中的业务语义并不完全相同，例如 `pre_close` 对期货通常对应昨结算，持仓字段对港股
没有同等含义。API 保留协议层稳定字段，不把未验证字段强行解释为某个市场的专有概念。

## 03. 市场报价列表

```python
# category=2 港股，category=3 期货
quotes = client.quotes(market=31, category=2, start=0, offset=100)
```

目前协议结构只验证了 `category=2` 和 `category=3`。真实节点单次最多返回 100 条，超过后会静默截断，
所以 next 客户端要求 `offset` 为 1～100。

旧 tdxpy 的该解析器会在多条期货报价上把结果列表误当成下一个字节游标，引发 `TypeError`；next
实现按每条固定 300 字节独立步进，并对完整响应长度做校验。

## 04. K 线

```python
bars = client.bars(
    symbol='31#00700',
    frequency='day',
    start=0,
    offset=100,
)
```

`frequency` 接受 `0`～`11` 以及 `5m`、`15m`、`30m`、`1h`、`1m`、`day`、`days`、`dk`、`week`、`mon`、
`3mon`、`year`。真实节点单次最多返回 700 条；请求 800 或更多时服务端仍只返回 700，因此 next 会对
`offset > 700` 明确报错。

协议中 `4` 和 `9` 都是日线，但成交量编码不同：`4` 是 alternate daily/share encoding，`9` 是标准
日线编码；字符串别名分别是 `days` 和 `day`/`dk`。不要把它们当成可互换的 wire value。`True`/`False`
不会再被当成整数频率接受。

结果包含 `datetime`、OHLC、`position`、`trade`、`settlement_price` 和 `amount`。协议中的
`amount` 是将 `position` 所在四个字节按 float 重新解释所得，与既有 TDX 解码方式一致；它对港股等
品类未必表示成交额，使用前应按市场验证。

## 05. 当前和历史分时

```python
current = client.minute(symbol='31#00700')
history = client.minutes(symbol='31#00700', date='20260729')
```

字段为 `time`、`hour`、`minute`、`price`、`average_price`、`volume`、`open_interest`。ExHq 响应不
携带当前分时的交易日期；凌晨仍可能返回上一交易日数据，因此 next 不使用本机日期合成一个可能错误的
`datetime`。对应市场非交易时段，当前接口允许为空。

## 06. 当前和历史逐笔

```python
current = client.transaction(symbol='31#00700', start=0, offset=100)
history = client.transactions(
    symbol='31#00700',
    date='20260729',
    start=0,
    offset=100,
)
```

当前和历史逐笔的真实单次上限均为 1800，更多数据请递增 `start` 分页。字段包括：

- `time`、`hour`、`minute`、`second`
- `price`：已经还原的实际价格
- `price_raw`：协议原始整数，等于 `price * 1000`
- `volume`、`position_change`
- `nature`、`nature_mark`、`nature_value`、`nature_name`、`direction`
- 历史逐笔额外提供 `datetime`

旧解码器直接返回原始整数，例如腾讯历史成交价 `466400`；与同期五档行情 `466.4` 对照后，next 修正
为 `/1000`，同时保留 `price_raw` 便于审计。当前逐笔不伪造交易日期，非交易时段允许为空。

## 07. 日期区间 K 线

```python
rows = client.bars_range(
    symbol='31#00700',
    start='2026-07-01',
    end='2026-07-29',
)
```

这是通达信 `0x240D` 接口。真实响应是日期范围内的分钟 K，而不是普通日 K；服务端单次可能截断，例如
实测较长区间返回 1500 条。结果含 `datetime`、OHLC、`position`、`trade` 和
`settlement_price`。如需完整长区间，调用方应拆分日期窗口。

tdxpy 0.2.7 的该公开方法会把解析器自身误设为 socket client，并以缺少 `send` 方法失败；next 已用
真实区间响应和独立协议测试覆盖这一接口。

## 08. 直接使用 next SDK

Raw 同步客户端返回 `list`/`dict`：

```python
from mootdx_next import ExSyncClient

client = ExSyncClient()
try:
    quote = client.quote(31, '00700')
    bars = client.bars(31, '00700', frequency='day', offset=100)
finally:
    client.close()
```

异步客户端与同步客户端方法完全对称：

```python
import asyncio

from mootdx_next import AsyncExClient


async def main():
    client = AsyncExClient()
    try:
        quote, bars = await asyncio.gather(
            client.quote(31, '00700'),
            client.bars(31, '00700', frequency='day', offset=100),
        )
    finally:
        client.close()


asyncio.run(main())
```

`ExPandasClient` 和 `AsyncExPandasClient` 提供相同业务接口并返回 DataFrame。异步实现只共享不可变的
候选 IP 快照；每个工作线程会在线程内部创建自己的 `ExSyncClient`、连接池和服务器健康状态。

`timeout` 是兼容层的秒单位，创建 native client 时会转换为 `timeout_ms`；`auto_retry=False` 会将 native
重试次数设为 0。`heartbeat=True` 会启动每 10 秒一次的轻量股票/品种计数请求来保持 TCP 会话；异步
客户端只为第一个工作线程启动一个心跳 worker。`raise_exception` 保留用于旧构造函数，native 客户端
统一抛出明确的异常。

调用 `close()` 后客户端处于终止状态，业务请求会抛出 `ClientClosedError`；需要继续使用时显式调用
`reconnect()`。这避免了把资源关闭误解为“下次请求自动重建”。

## 09. 真实验证

扩展行情测试不会放在 GitHub Actions，因为海外 runner 通常无法连接通达信节点。本地执行：

```bash
MOOTDX_NEXT_EX_LIVE=1 pytest tests/core_engine/test_next_ex_live_smoke.py -q
```

该矩阵覆盖市场、品种计数、分页和全量品种、单品种/列表报价、K 线、当前/历史分时、当前/历史逐笔、
日期区间 K、并发异步工作线程以及 `Quotes.factory(market='ext')` 的 Pandas 结果。
