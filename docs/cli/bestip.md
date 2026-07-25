# 线路测试

## 查看命令帮助

```shell
mootdx bestip --help

Usage: mootdx bestip [OPTIONS]

  测试行情服务器.

Options:
  -l, --limit INTEGER  显示最快前几个，默认 5.
  -v, --verbose        详细模式
  -h, --help           Show this message and exit.
```

## 主动刷新候选线路

```shell
mootdx bestip -v
```

命令会验证节点是否同时支持 `600036` 实时行情和日 K 线，按响应时间排序，并主动刷新 next 引擎的
进程级候选 IP 快照。快照保存在当前 Python 进程内，默认有效期为 10 分钟。

命令保留为旧调用入口，但不再提供 `-w`，也不会写入 `~/.mootdx/config.json` 的旧版 `BESTIP` 配置。
其他进程不会继承本次命令的内存快照；应用内需要测速时，应在创建客户端时使用
`Quotes.factory(bestip=True)` 或 `PandasClient(bestip=True)`。
