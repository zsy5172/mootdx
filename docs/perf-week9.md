# Week 9 性能分析记录

测量日期：2026-04-12  
测量环境：本地 `uv` 开发环境，Python 3.13，分支 `next/core-engine`  
在线节点：`110.41.174.169`、`175.178.128.227`、`116.205.171.132`、`116.205.163.254`、`116.205.183.150`

## 范围

Week 9 的性能工作只覆盖两类负载：

- 在线端到端基准：`finance("000001")`、`xdxr("600036")`
- 离线 replay decode profiler：`quotes`、`bars`、`transactions`、`finance`、`xdxr`

测量命令：

```bash
uv run python scripts/benchmark_info_apis.py --iterations 10
uv run python scripts/profile_replay_decoders.py --iterations 1000 --top 8
```

## 在线基准

`finance("000001")`

- `avg_ms=12.257`
- `p50_ms=8.945`
- `p95_ms=9.571`
- `rows_per_sec=81.587`

`xdxr("600036")`

- `avg_ms=9.109`
- `p50_ms=9.054`
- `p95_ms=9.510`
- `rows_per_sec=7245.843`

说明：

- `finance` 只有单行结果，`rows_per_sec` 只作为粗略吞吐指标。
- `avg_ms` 高于 `p95_ms`，说明首个请求的握手/热身开销明显；稳定态延迟更接近 `p50/p95`。
- 在线基准没有看到 transport 或 scheduler 异常放大，Week 9 的主要瓶颈不在连通性。

## 离线 Replay Profiler

主要结果：

- `quotes-mixed-batch`：`avg_decode_ms=1.711552`，热点主要在 `quotes_to_frame()` 和 `pandas.DataFrame(...)`
- `bars-daily-10`：`avg_decode_ms=0.615125`，热点主要在 `bars_to_frame()` 和 pandas 构造
- `transactions-history-10`：`avg_decode_ms=0.290603`，热点主要在 `transactions_to_frame()` 和 pandas 构造
- `finance-single-row`：`avg_decode_ms=0.905220`，几乎全部时间都花在 `finance_to_frame()` 和 pandas 构造
- `xdxr-66-rows`：`avg_decode_ms=1.257885`，除 `xdxr_to_frame()` 外，`StdQuoteProtocol.decode_xdxr()` 本身已进入主要热点

结论：

- 当前离线 decode 的最大成本不是 transport，也不是大多数 parser，而是 adapter 层把轻量结果转成 DataFrame。
- `quotes`、`bars`、`transactions`、`finance` 四类 workload 都呈现同样模式：pandas 物化占主导。
- `xdxr` 是例外。它的 DataFrame 物化仍然是第一热点，但 `decode_xdxr()` 自身已经有可见成本，后续可以单独微优化。

## 低风险优化建议

优先级 1：

- 保持核心层默认返回轻量对象，不在主链路默认转 DataFrame。
- replay 和 compat 之外的调用路径尽量晚一点进入 adapter。

优先级 2：

- 在高频 parser 中继续复用 `struct.Struct`，减少重复创建。
- 减少 `decode_xdxr()` 里的重复切片和临时对象构造。

优先级 3：

- 审查 adapter 是否存在可省略的列重排或重复字段复制。
- 如果后续 benchmark 仍显示 DataFrame 为绝对主热点，再考虑把 compat 正常化路径改成更轻量的表格表示。

## Week 10 前的判断

- Week 9 的性能分析已经足够支持一个判断：现阶段不需要优先改 transport 或 scheduler。
- 如果 Week 10 进入兼容层预备阶段，性能优化应首先围绕 adapter 和 `xdxr` parser 展开，而不是继续重构连接管理。
