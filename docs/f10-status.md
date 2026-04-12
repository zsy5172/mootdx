# F10 接口现状记录

本文档记录 `F10` / `F10C` 在当前兼容阶段的实现边界、验证方式和待处理问题。

这不是明确 bug 清单，而是阶段性现状说明。等标准市场兼容、参数矩阵和 corpus 进一步稳定后，再决定是否把其中部分问题升级为 breaking change 修复项。

## 当前结论

- `next engine` 已实现：
  - `f10_categories`
  - `f10_content`
  - 兼容层 `F10C`
  - 兼容层 `F10`
- 当前验证方式以 **replay/corpus** 为主，不把 F10 纳入 live 强门禁。

## 已有记录

- 成功的 `F10C` corpus：
  - [compat/specs/f10_categories/sh_600036.json](/mnt/c/Users/eric/PycharmProjects/mootdx/compat/specs/f10_categories/sh_600036.json)
  - [compat/corpus/f10_categories/sh_600036/manifest.json](/mnt/c/Users/eric/PycharmProjects/mootdx/compat/corpus/f10_categories/sh_600036/manifest.json)
- 成功的 `F10` corpus：
  - [compat/specs/f10_content/sh_600036__latest_tip.json](/mnt/c/Users/eric/PycharmProjects/mootdx/compat/specs/f10_content/sh_600036__latest_tip.json)
  - [compat/corpus/f10_content/sh_600036__latest_tip/manifest.json](/mnt/c/Users/eric/PycharmProjects/mootdx/compat/corpus/f10_content/sh_600036__latest_tip/manifest.json)
- 空结果 `F10C` corpus：
  - [compat/specs/f10_categories/sz_000001_empty.json](/mnt/c/Users/eric/PycharmProjects/mootdx/compat/specs/f10_categories/sz_000001_empty.json)

## 当前限制

### 1. F10 live 节点稳定性不足

- 同一接口在不同节点上可能直接返回空目录。
- 因此当前不把 `F10` / `F10C` 放进 `compat_live_smoke`。
- 现阶段只要求 replay parity 通过。

### 2. 旧版错误语义较弱

- 旧版 `F10()` 先依赖 `F10C()`。
- 如果目录为空，旧版直接返回 `None`。
- 这会混淆“真实无数据”和“节点临时返回空”的情况。

### 3. 旧版 `F10(name=...)` 未命中时语义异常

- 当 `name` 不存在时，旧版不会报错。
- 它会退化成“返回整本 F10 字典”。
- 兼容层当前保留该行为，以避免提前引入 breaking change。

### 4. 市场范围有限

- 当前只支持沪深。
- 北交所不在 `F10` / `F10C` 支持范围内。

## 当前策略

- 继续保留 replay-only 验证方式。
- 不把 F10 live 稳定性问题误判为协议实现错误。
- 兼容阶段优先保持旧版行为，包括：
  - `F10C` 空目录返回空结果
  - `F10(name)` 未命中时退化成整本字典

## 后续处理建议

- 等 nightly 和标准接口切换条件成熟后，再评估以下变更：
  - 是否引入 F10 的 live probe，而不是 live 强门禁
  - 是否把“目录为空”和“节点异常”拆成不同错误语义
  - 是否将 `F10(name)` 未命中改为显式异常，而不再回落成整本字典
