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
```

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
原生前复权连续扣除历史现金分红，因此很早的价格可能为负数。两组模式都不调整成交量和成交额。

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
