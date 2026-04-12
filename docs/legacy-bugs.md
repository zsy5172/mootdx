# 旧版行为缺陷记录

本文档用于记录在 legacy `mootdx/pytdx/tdxpy` 路径中确认存在、但暂时**不在兼容阶段修复**的行为缺陷。

当前策略：

- `next engine` 在兼容阶段优先保持与 legacy 一致，避免过早引入 breaking change。
- 已确认的缺陷先在这里归档。
- 等现有接口接管、参数矩阵、corpus 和回归体系稳定后，再统一评估修复窗口。

## 记录规则

- 只记录已确认的行为问题，不记录纯猜测。
- 每条问题至少写清：位置、现象、影响、当前处理策略、后续修复建议。
- 如果 `next engine` 为了兼容而复制了该行为，需要明确标注。

## `time_frame()` 交易时段边界判断错误

- 位置：
  - [tdxpy/helper.py](/mnt/c/Users/eric/PycharmProjects/mootdx/.venv/lib/python3.13/site-packages/tdxpy/helper.py)
  - [mootdx_next/session.py](/mnt/c/Users/eric/PycharmProjects/mootdx/mootdx_next/session.py)
- 现象：
  - 旧版使用严格开区间判断交易时段：
    - `09:30 < t < 11:30`
    - `13:00 < t < 15:00`
  - 这会把 `09:30` 和 `13:00` 判成非交易时段。
- 影响：
  - 以交易语义看，上午和下午开盘时刻都应视为可请求实时接口。
  - 旧版在这两个边界时刻会错误地拒绝实时分笔等接口。
- 当前处理：
  - `next engine` 当前仍复制 legacy 逻辑，以保证兼容阶段回归稳定。
  - 该问题暂不在兼容阶段修复。
- 后续修复建议：
  - 调整为：
    - `09:30 <= t < 11:30`
    - `13:00 <= t < 15:00`
  - 修复时应同步补充边界测试：`09:30`、`11:30`、`13:00`、`15:00`。
  - 该修改应按 breaking change 管理，并在切换默认引擎前统一处理。
