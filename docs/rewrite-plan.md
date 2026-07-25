# 核心重构开发计划

## 背景与目标

当前 `mootdx` 在线行情能力依赖 `tdxpy/pytdx` 生态，已经暴露出几个明确问题：

- 失败语义不清晰，底层调用在部分场景下会吞异常并返回 `None`。
- 单连接模型以同步串行为主，`multithread=True` 更多是线程安全保护，不是高吞吐并发模型。
- 协议组包、收包、解析、重试、节点选择、DataFrame 转换耦合较紧，难以做高性能和高可观测性重构。
- 未来 Python 版本兼容依赖旧栈演进节奏，不可控。

重构分支 `next/core-engine` 的终局目标是：逐步摆脱 `tdxpy/pytdx` 作为运行时核心依赖，建立自有协议引擎、连接池和对比测试体系，并在新核心稳定后提供兼容层接管旧 API。

## 当前状态

本计划的 SDK 解耦和兼容层目标已经实现：

- `mootdx_next` 是独立生产代码树，AST 门禁和屏蔽旧命名空间的子进程导入测试禁止任何
  `mootdx_next → mootdx` 依赖。
- 同一个 `mootdx` wheel 同时发布 `mootdx` 与 `mootdx_next`；旧 API、CLI 和兼容入口只允许单向调用 next。
- `SyncClient`/`AsyncClient` 提供 Raw 结果，`PandasClient`/`AsyncPandasClient` 提供 DataFrame 结果，
  同步与异步业务接口由库存测试强制保持对称。
- 标准市场 `Quotes.factory()` 已默认使用 next；`get_k_data`、`k`、`ohlc` 各自保留原公开签名。
- 本地标准/扩展 Reader、板块、自定义板块和财务文件解析已经迁入 next，旧模块仅为 re-export 或薄包装。
- `bestip=True` 使用进程级线程安全候选注册表，懒测速、缓存不可变快照 10 分钟，不写旧版配置。
- legacy baseline 固定为 `Python 3.11 + mootdx 0.11.7 + tdxpy 0.2.7`；默认 exact，
  只有登记到 deviation registry 的原版缺陷修复可以偏离。

下文的周级阶段保留为设计与实施历史；当前验证命令和覆盖范围以
[Next engine test matrix](next-test-matrix.md) 为准。

## 设计原则

### 1. 新核心优先，兼容层后置

新分支先构建 clean-slate core，不让旧 API 形状反向束缚协议层和调度层设计。兼容层只在核心稳定并完成足够 parity 后再引入。

### 2. 运行时不直接依赖 legacy baseline

新运行时环境不再把 `tdxpy/pytdx` 作为对比工具直接 import。旧实现仅存在于 Docker baseline 中，所有兼容性验证都通过标准化 artifact 完成。

### 3. 显式错误优于隐式空值

新核心必须区分：

- 连接失败
- 超时
- 协议解析失败
- 空响应
- 不支持市场
- 交易时段限制

禁止使用 `None` 作为网络失败的默认兜底。

### 4. 轻量结果优于默认 DataFrame

核心层默认返回轻量 Python 结构，`pandas.DataFrame` 只存在于适配层。这样才能降低 CPU、内存和对象分配成本，也更方便双轨标准化比较。

### 5. replay 优先于 live

日常开发回归以确定性的 replay 和 artifact 比较为主。live 对比用于 smoke 和 nightly，避免交易时段、节点波动和实时价格变化污染每次提交反馈。

## 目标架构

新核心按五层拆分，依赖方向必须单向向下：

### transport

负责 TCP 连接、收发包、超时、断线重连、心跳和底层流量统计。

输出：

- 原始响应头
- 原始响应体
- 连接状态和错误

### protocol

负责每个接口的组包和包体解析，不负责节点选择、不负责 DataFrame 转换。

输出：

- 轻量结果对象
- 协议级错误

### scheduler

负责连接池、server pool、健康评分、限流、熔断、重试和负载分发。

输出：

- 针对某个请求选择好的连接
- 标准化的调度错误与指标

### api

负责高层用户接口组织，如 `stock_count`、`stocks`、`quotes`、`bars` 等。这里只拼接调用流程，不嵌入 socket 细节和 pandas 逻辑。

### adapters

负责：

- DataFrame 转换
- 旧版 `mootdx` API 兼容层
- CLI 适配

## 计划中的公共接口

后续实现阶段以以下公共对象为目标：

- `SyncClient`
- `AsyncClient`
- `ConnectionPool`
- `ServerPool`

这几个对象属于新核心公共入口，兼容层将围绕它们构建，而不是直接暴露底层 parser。

## compat 子系统职责

`compat/` 目录在整个重构周期内承担基线验证职责：

- `specs/`：定义 API 对比输入
- `runners/`：执行本地与 baseline 调用并输出 artifact
- `docker/`：固定 legacy baseline 运行环境
- `artifacts/`：保存生成结果
- comparator：比较标准化产物，而不是比较运行时内部对象

后续应继续扩展为完整的兼容性测试库，而不是一次性的 smoke 脚本。

## 分阶段实施计划

### Phase 1：环境与基线

目标：建立独立于旧运行时的开发、测试和基线基础设施。

已完成内容：

- `pyproject.toml` 已切换到 `hatchling`
- `uv.lock` 已生成
- `noxfile.py` 已提供 `unit`、`compat_replay`、`baseline_capture`、`compat_live_smoke`
- Docker baseline 已可运行 `stock_count` smoke case

完成标准：

- 新分支安装、构建、测试不再依赖 Poetry
- legacy baseline 可重复构建
- baseline 与本地产物可比较

### Phase 2：transport 与 scheduler 核心

目标：先把“怎么连、怎么发、怎么收、怎么重试、怎么选节点”重写出来。

本阶段必须交付：

- 明确的连接生命周期管理
- 多连接池，而不是单 client 共享锁
- server pool 与健康评分
- 重试、超时、熔断、心跳
- 标准化错误模型
- 基础指标：请求数、失败数、超时率、重连次数、延迟分位数

不包含：

- 全量接口覆盖
- 旧 API 兼容层

完成标准：

- transport 层可独立压测
- scheduler 行为可通过模拟和单元测试覆盖
- 不依赖 DataFrame 即可完成一次请求生命周期

### Phase 3：高频协议覆盖

目标：优先拿下最有价值、最常用、最能暴露设计问题的接口。

实现顺序固定为：

1. `stock_count`
2. `stocks`
3. `quotes`
4. `bars`
5. `minute` / `minutes`
6. `transactions` / history transaction
7. finance、F10、ext market 相关接口

每新增一个接口，都必须同时新增：

- 对应 spec
- 对应 replay corpus
- baseline 对比结果
- 明确的异常场景测试

完成标准：

- 高频接口在 replay 上达到稳定 parity
- live smoke 覆盖主要正向路径
- 关键字段精度和结构已锁定

### Phase 4：兼容层接管

目标：在新核心足够稳定后，提供旧 API 的适配层。

本阶段必须交付：

- 与现有 `mootdx` 公共 API 兼容的薄适配
- pandas 转换适配
- CLI 入口适配
- 文档迁移说明

开始条件：

- replay 通过率达到门槛
- 性能门槛达到要求
- nightly 稳定通过足够天数

## 双轨对比测试方案

### spec 设计原则

每个 API 都需要以下四类 case：

- 代表用例：常见参数组合
- 边界用例：最小值、最大值、分页边界、空值、冷门市场
- 非法用例：错误 market、错误 symbol、交易时段不支持等
- live-only 用例：不适合 replay 的真实链路校验

不做全量笛卡尔积。参数组合采用“代表集 + 边界集 + pairwise 补充”。

### artifact schema

所有 runner 输出统一 artifact，最少包含：

- `backend`
- `api`
- `status`
- `result_type`
- `result`
- `error`
- `spec`
- `captured_at`

后续扩展项：

- `server`
- `latency_ms`
- `raw_header`
- `raw_body_ref`
- `transport_stats`

### 测试分层

#### unit

只测：

- parser
- 编解码
- 错误模型
- scheduler 决策逻辑

不访问真实网络。

#### compat-replay

基于固定响应体和基线 artifact 做确定性比对。这是每次开发必须跑的主门禁。

#### compat-live-smoke

只跑少量真实接口，对链路连通性、基线 runner、产物格式进行健康检查。

#### nightly compat-live

每天全量采样运行，用于发现远端协议变化、节点质量问题和实时链路偏差。

### 比较规则

比较器必须按接口类型分流：

- 列表类：精确比较结构与字段
- 历史数据类：按主键对齐后比较
- 浮点字段：允许小范围容差
- 实时行情类：replay 做精确比对，live 只校验形状、字段和错误语义

## 性能目标与验收门槛

性能比较对象固定为 Docker legacy baseline，不与“主观体感”比较。

第一阶段量化目标：

- 高频列表/行情工作负载端到端吞吐达到 legacy baseline 的 5x 以上
- 单请求延迟不得比 baseline 回退超过 10%
- replay 对比通过率达到 99.5% 以上
- nightly compat-live 连续 7 次通过后，才允许推进兼容层

后续更高目标可以再提升，但第一阶段先以“稳定显著优于 baseline”为准。

## 风险与约束

### 远端数据不稳定

实时行情、交易时段接口、坏节点、临时超时都会影响 live 结果，因此 live 不能作为唯一真相源。

### 旧实现本身存在不确定性

legacy baseline 只是现阶段参考实现，不代表完全正确。文档、协议实测和多次重放结果需要一起作为判定依据。

### 分支期间不做一次性替换

必须遵循“先双轨、后替换、最后删除”的策略，避免直接把新核心硬切进旧运行时。

## 后续维护清单

1. 默认 CI 持续运行单元测试、依赖边界门禁和确定性 corpus replay。
2. 新增或修改公共方法时同步更新 Raw、Pandas、同步、异步和参数库存矩阵。
3. 原版差异默认视为回归；只有具备证据、回归用例并登记 deviation registry 后才可接受。
4. 人工或 nightly 执行真实行情矩阵；实时逐笔只在交易时段验证，F10/F10C 固定使用 `600036`。
5. 在线 EX 保持 unsupported，本地 `ExtReader` 继续维护；旧 GP socket 不恢复，财务文件使用官方 HTTPS。

每一步都应以“小批次可提交”为单位推进，不接受“大重写后统一验证”的方式。

## 本文档的使用方式

- 作为 `next/core-engine` 分支的总计划文档
- 所有后续阶段变更都要回写此文档
- 当阶段目标、验收门槛或测试策略发生变化时，优先更新本文档，再推进代码实现

## 周级开发排期

以下排期以“单周有明确产出、每周末有可验证结果”为原则编排。若某周未达到退出条件，不进入下一周功能扩展，优先补齐门槛。

### Week 1：核心骨架与错误模型

目标：

- 建立 `transport`、`protocol`、`scheduler`、`api`、`adapters` 的目录骨架
- 定义基础异常类型、标准结果对象和指标字段
- 明确连接对象、请求对象、响应对象的数据结构

交付物：

- 新核心模块空实现与接口占位
- 标准错误模型初稿
- 第一版内部接口约定文档或模块注释

退出条件：

- 核心骨架可导入
- 单元测试可覆盖错误模型和基础对象
- 不影响现有 `mootdx` 运行时

当前进度：

- 已新增 `mootdx_next/transport`、`protocol`、`scheduler`、`api`、`adapters` 目录骨架。
- 已新增基础 dataclass、错误层级和抽象接口。
- 已补充 `tests/core_engine/` 覆盖导入、默认值、继承关系和占位行为。

### Week 2：transport 最小可用链路

目标：

- 完成底层连接、发送、接收、超时和断开逻辑
- 建立响应头解析、响应体读取和基础心跳能力

交付物：

- 单连接最小可用 transport
- transport 层单元测试与基础压测脚本
- 首批 transport 指标输出

退出条件：

- 可完成一次完整请求收发
- 断线、超时、空响应路径有明确异常
- transport 层不依赖 DataFrame

当前进度：

- 已实现 `SyncSocketTransport`、`ResponseHeader` 和 transport 级错误映射。
- 已新增 fake socket 单元测试、live smoke 测试与手工 benchmark 脚本。
- `SyncClient` 与 `AsyncClient` 仍保持占位，本周不接入 protocol 与 scheduler。

### Week 3：scheduler 与连接池

目标：

- 引入多连接池、server pool、健康评分和重试策略
- 完成调度层的限流、熔断、坏节点剔除逻辑

交付物：

- `ConnectionPool`、`ServerPool` 初版
- scheduler 级别模拟测试
- 延迟、错误率、重连次数等指标打通

退出条件：

- 同一工作负载可稳定分发到多连接
- 坏节点可降权或剔除
- replay/live smoke 的 runner 可接入新 transport + scheduler

当前进度：

- 已实现 `ConnectionPool` 初版，支持 lease、复用、池满快速失败和快照统计。
- 已实现 `ServerPool` 初版，支持基础熔断、冷却时间和按活跃连接数/延迟选节点。
- 已新增 scheduler 级单元测试、live smoke 和手工 benchmark 脚本。

### Week 4：replay 体系与首批 corpus

目标：

- 完善 `compat` 测试库，支持 replay corpus、标准 artifact、分类比较器
- 录制第一批高频接口的 baseline 响应

交付物：

- `stock_count`、`stocks`、`quotes` 的 spec 与 replay corpus
- 基于 artifact 的比较器初版
- `compat-replay` 扩展为真正的主回归入口

退出条件：

- 上述三个接口可稳定做 baseline 对比
- replay 在无网络条件下可重复运行
- 比较结果能明确指出字段差异或异常语义差异

当前进度：

- 已实现 `compat/specs`、`compat/corpus`、capture/replay runner 和结构化 comparator。
- 已录制 `stock_count`、`stocks`、`quotes` 的首批 baseline corpus。
- `compat_replay` 已升级为默认全量离线回归入口，`compat_live_smoke` 继续保留在线健康检查。

### Week 5：实现 `stock_count`、`stocks`

目标：

- 完成首批协议解析实现
- 用新核心打通列表类接口

交付物：

- `stock_count`、`stocks` 新实现
- 对应单元测试、replay 对比、live smoke case
- 边界条件测试：非法 market、空返回、分页边界

退出条件：

- 两个接口 replay 对比通过率达到 100%
- live smoke 连续多次稳定通过
- `stocks` 不再因底层异常退化为 `None > 0` 这类问题

当前进度：

- 已实现 `StdQuoteProtocol` 的 `stock_count`、`stock_list_page` 编解码。
- 已实现 `SyncClient.stock_count()` 和 `SyncClient.stocks()` typed API，打通 `scheduler + connection_pool + transport + protocol` 链路。
- 已新增 `next` replay backend，并接入 `compat_replay` 对 `stock_count/*`、`stocks/*` corpus 的离线 parity 校验。
- 已补齐编码、解码、分页边界、错误传播和 live smoke 测试，`stocks` 的空计数路径已显式返回空列表。

### Week 6：实现 `quotes`

目标：

- 完成实时行情接口新实现
- 建立实时字段标准化规则

交付物：

- `quotes` 新实现
- 实时行情 replay 样本与 live smoke case
- 字段精度、空值和排序规则文档化

退出条件：

- replay 精确对比通过
- live 只在允许波动的字段策略下通过
- 吞吐与延迟开始纳入基准统计

当前进度：

- 已实现 `StdQuoteProtocol` 的 `quotes` 编解码，并补齐 symbol 市场归一化规则。
- 已实现 `SyncClient.quotes()` typed API，支持单 symbol 与 batch 输入，保持输入顺序。
- 已将 `quotes/single_sh`、`quotes/mixed_batch` 接入 `compat_replay` 的 next parity。
- 已新增 quotes 的同包 live 双解码校验和端到端 live smoke，避免用两次独立实时请求做逐字段比较。

### Week 7：实现 `bars`、`minute`、`minutes`

目标：

- 完成高频历史与分时接口
- 建立基于主键对齐的比较器

交付物：

- `bars`、`minute`、`minutes` 新实现
- 历史数据 replay corpus
- 按 `(code, datetime)` 或等价主键对齐的比较规则

退出条件：

- 历史/分时接口 replay 通过率达到阶段门槛
- 浮点容差规则稳定
- 关键工作负载吞吐达到 baseline 明显提升

当前进度：

- 已实现 `StdQuoteProtocol` 的 `bars`、`minutes` 编解码，并完成 `minute()` 对当天 `minutes()` 的包装。
- 已实现 `SyncClient.bars()`、`SyncClient.minutes()`、`SyncClient.minute()` typed API，参数校验和错误语义固定到新核心。
- 已新增 `bars` 与 `minutes` 的 replay corpus，并接入 `compat_replay` 的 next parity。
- 已新增 `bars`/`minutes` 的同包 live 双解码校验、端到端 live smoke 与历史接口 benchmark 脚本。

### Week 8：实现 transaction/history 与稳定性收尾

目标：

- 补齐分笔及历史分笔接口
- 强化断线重连、时段限制和异常语义

交付物：

- `transactions` 与 history transaction 新实现
- 时段限制相关测试
- 更完整的 nightly compat-live 样本集

退出条件：

- 高频在线接口全部纳入双轨体系
- nightly 可连续运行
- 错误模型覆盖主要失败路径

当前进度：

- 已实现 `StdQuoteProtocol` 的 `transaction` 与 `transactions` 编解码，并接入新核心 typed API。
- 已实现 `SyncClient.transaction()`、`SyncClient.transactions()`，补齐 SH/SZ 限制、BJ 显式拒绝、历史日期校验与实时分笔时段限制。
- 已为 transport/pool 级失败补上一轮自动重试，并支持在单次请求内排除已失败节点重新选服。
- 已新增 transaction/history transaction 的 replay corpus，并接入 `compat_replay`、history live decode parity 与 session-aware realtime smoke。

### Week 9：扩展接口与性能调优

目标：

- 继续补 finance、F10、ext market 等次高频接口
- 开始集中做性能分析与热点优化

交付物：

- 次高频接口分批接入
- profiler 报告
- 针对解析和连接池的第一轮优化结果

退出条件：

- 第一批性能目标接近或达到门槛
- baseline 对比覆盖范围继续扩大
- 未解决问题形成明确 backlog

当前进度：

- 已实现 `finance`、`xdxr`、`f10_categories`、`f10_content` 的 `StdQuoteProtocol` 编解码，并补齐 SH/SZ 限制、未知 F10 栏目异常和 typed API。
- 已为 `finance`、`xdxr`、`f10_categories`、`f10_content` 新增 replay spec 与 corpus，`compat_replay` 已覆盖这些接口的 next parity。
- `compat_live_smoke` 已新增 `finance` 与 `xdxr` 的同包 live 双解码校验；F10 在普通 CI 使用 replay，
  在 opt-in 完整 live matrix 中固定使用 `600036`。
- 已新增 `scripts/benchmark_info_apis.py` 与 `scripts/profile_replay_decoders.py`，并形成 [Week 9 性能记录](perf-week9.md)。
- 当前性能分析结论已收敛：多数 workload 的主要热点在 adapter 层 DataFrame 物化，`xdxr` parser 本身是少数仍需继续盯的协议热点。

### Week 10：兼容层预备与阶段验收

目标：

- 评估是否满足进入兼容层阶段的条件
- 整理接口覆盖率、replay 通过率、nightly 稳定性和性能数据

交付物：

- 阶段验收报告
- 兼容层设计草案
- 下一阶段任务拆分清单

退出条件：

- replay 对比通过率达到文档门槛
- nightly compat-live 达到连续通过要求
- 性能基准达到预期下限
- 满足后才进入旧 API 兼容层开发

当前进度：

- 已实现 `Quotes.factory(market="std", engine="next")` 的标准市场兼容层，标准市场默认 factory 路径已经切到 next。
- 已通过兼容层接管 `quotes`、`bars`、`stock_count`、`stocks`、`stock_all`、`minute`、`minutes`、`transaction`、`transactions`、`F10C`、`F10`、`xdxr`、`finance`、`index`、`index_bars`、`block`、`k`、`ohlc`、`get_k_data`。
- 标准市场旧接口当前已全部进入 next engine 兼容层，未覆盖范围主要剩余扩展市场和后续 breaking change 修正项。
- 已补充兼容层单测、旧入口 live smoke、阶段验收报告与 nightly workflow；后续解耦、矩阵和默认切换验收均已通过。

## 周级执行规则

- 每周开始前固定本周目标接口与测试目标，不在周中随意扩 scope。
- 每周结束前必须至少更新一次本文档中的进度与风险状态。
- 每周交付必须包含代码、测试和 baseline 对比结果三部分，不能只交代码。
- 若当周退出条件未达成，下周优先补齐，不并行开启新接口。
