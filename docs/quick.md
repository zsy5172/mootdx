# 快速上手

## 使用最快的服务器

```shell
python -m mootdx bestip -vv
```

该命令主动刷新当前进程的 next 候选 IP 注册表，结果缓存 10 分钟，不会写入旧版 `BESTIP` 配置。

## 离线数据读取

```python
from mootdx.reader import Reader

# market: 参数 `std` 为标准市场(就是股票), `ext` 为扩展市场(期货，黄金等)
# tdxdir: 是通达信的数据目录, 根据自己的情况修改

reader = Reader.factory(market='std', tdxdir='C:/new_tdx')

# 读取日线数据
reader.daily(symbol='600036')

# 读取分钟数据
reader.minute(symbol='600036')

# 读取时间线数据
reader.fzline(symbol='600036')
```

## 线上行情读取

```python

from mootdx.quotes import Quotes

# 标准市场
client = Quotes.factory(market='std', multithread=True, heartbeat=True, bestip=True, timeout=15)

# k 线数据
client.bars(symbol='600036', frequency=9, offset=10)

# 指数
client.index(symbol='000001', market=1, frequency=9)

# 分钟
client.minute(symbol='000001')

```

## 财务数据读取

```python

from mootdx.affair import Affair

# 远程文件列表
files = Affair.files()

# 下载单个
Affair.fetch(downdir='tmp', filename='gpcw19960630.zip')

# 下载全部
Affair.fetch(downdir='tmp')
```

财务目录和文件由 next 财务客户端通过通达信官方 HTTPS 服务读取，并校验文件大小与 MD5。
