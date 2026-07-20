# Week 10 阶段验收报告

## 结论

- 标准市场 next engine 兼容层 MVP：`Go`
- 标准市场默认切换到 next engine：`No-Go`
- 扩展市场 next engine：`No-Go`

当前建议：

- 继续保持 `Quotes.factory()` 默认走 legacy
- 只允许通过 `Quotes.factory(market="std", engine="next")` 显式试运行新兼容层

## 当前覆盖范围

### 已覆盖的旧标准接口

- `quotes`
- `bars`
- `stock_count`
- `stocks`
- `stock_all`
- `minute`
- `minutes`
- `transaction`
- `transactions`
- `F10C`
- `F10`
- `xdxr`
- `finance`
- `index_bars`
- `index`
- `block`
- `k`
- `ohlc`
- `get_k_data`
- `close`
- `reconnect`
- `closed`

### 尚未接管的旧标准接口

- 当前标准市场旧接口已全部接入 `engine="next"` 兼容层。
- 尚未覆盖的范围主要在扩展市场和后续 breaking change 修正项，不在本节列出。

### 扩展市场

- `market="ext"` 继续只支持 legacy
- `market="ext", engine="next"` 会显式失败

## 验收依据

关键门禁：

- `unit`
- `compat_replay`
- `compat_live_smoke`
- 旧入口兼容层单测
- 旧入口兼容层 live smoke

辅助依据：

- [Week 9 性能记录](perf-week9.md)
- `compat_nightly` 会话与对应 nightly workflow

## 为什么现在不能默认切换

- 仓库内还没有连续 7 次 `compat_nightly` 通过记录
- 扩展市场完全未纳入 next engine
- Week 9 的性能结论已经说明 adapter 层 DataFrame 物化仍是主要热点，默认切换前还应继续压缩兼容层开销

## 下一阶段建议

- 先累计 nightly 历史记录，再重新评估默认切换
- 优先继续扩参数矩阵、live 样本和 breaking change 待办清单
- 如果继续做性能优化，优先看兼容层 DataFrame 物化和 `xdxr` parser
