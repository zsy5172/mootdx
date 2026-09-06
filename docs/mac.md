# MAC 协议接口

`mootdx_next` 同时支持标准 TDX 行情协议和通达信 MAC 应用协议。MAC 不是网卡
地址或 MACD 指标，而是项目对 `0x1C` 请求帧及 `0x12xx/0x25xx` 消息族的命名。

```python
from mootdx_next import MacField, MacFieldPreset, SyncClient

client = SyncClient()
rows = client.mac_board_members(
    "881001",
    fields=MacFieldPreset.FUND_FLOW,
)
```

MAC 返回的是服务器提供的动态报价字段；板块汇总和排名应由调用者根据
`mac_board_members()` 结果自行聚合。它与本地文件驱动的 `block_catalog()` /
`block_members()` 以及标准 `fund_flows()` 是不同的数据来源和协议路径。
因此 `mac_board_summary()`、`mac_board_ranking()` 和
`mac_board_change_ranking()` 不属于 MAC 原始网络命令；如果需要这些便捷结果，
应在本地调用 `aggregate_block_quotes()` 或自行按成分股聚合。`block_catalog()`
是本地板块目录，`mac_board_list()` 则是实时 MAC 板块目录，两者不保证数量和
更新时间一致。

`mac_board_list()` 的 `sort_column` 使用独立的 `MacBoardSortColumn` 枚举，支持涨幅、
涨速、3/5/10/20/60 日涨幅和年初至今涨幅。返回记录中的 `sort_value` 与
`symbol_sort_value` 表示当前所选排序列的值，并不固定表示涨速。

常用接口包括：

* `mac_quotes()` / `mac_quotes_list()`：动态字段报价和分类排行；
* `mac_board_list()` / `mac_board_members()` / `mac_belong_board()`：实时板块关系；
* `mac_capital_flow()`：`Stock_ZJLX` 个股资金流；
* `mac_bars()` / `mac_tick_chart()` / `mac_tick_charts()` / `mac_transactions()`；
* `mac_auction()` / `mac_unusual()` / `mac_server_info()`；
* `mac_file_meta()` / `mac_file_chunk()` / `mac_file()` / `mac_goods_list()`。

`mac_unusual()` 同时返回服务器类型码 `unusual_type`、粗粒度名称 `unusual_type_name`、具体描述
`description` 和格式化数值 `value`。19 种已确认类型的公共映射可从顶层导入：

```python
from mootdx_next import UNUSUAL_TYPE_NAMES
```

其中 `0x13` 会区分竞价试买/试卖，`0x15` 按记录时刻区分竞价/尾盘，`0x16` 区分盘中强势/弱势，
`0x1D` / `0x1E` 分别表示急速拉升/下跌；未知类型仍保留原始数值码。

MAC 原生可用 `MacPeriod.MINS` 配合 `times=120` 请求 120 分钟 K 线；已有分钟记录也可交给
`aggregate_bars(rows, "120m")` 或 `aggregate_bars(rows, "120min")` 本地聚合。

列表、板块成分、K 线和逐笔接口的 `count` 表示调用者希望取得的总条数。客户端会按协议单页上限
自动分页，不会将大于 80 / 150 / 700 / 1000 的请求静默缩小。其中排序报价和逐笔保持服务器顺序；
K 线分页后仍按从旧到新的时间顺序返回。

同步原生客户端返回 `list[dict[str, object]]`，Pandas 客户端返回
`DataFrame`，异步客户端通过现有 worker 模型提供相同的方法。

扩展市场客户端（`ExSyncClient` / `AsyncExClient` 及其 Pandas 适配器）使用独立
的 7727 MAC 会话。第一次请求前会发送 `0x2454` 登录帧，帧头标记为 `0x01`；
它不能复用 A 股 7709 MAC 连接，也不会落到标准行情或 `fund_flows()` 的补充节点。
港股股票类市场的 `mac_transactions()` 是唯一例外：由于 7727 的 `0x122F` 不接入
港股逐笔数据，它兼容回退到现有 ExHq 当日 `0x23FC` / 历史 `0x2406`，并规范化为
MAC 逐笔字段；这不是把 ExHq 消息伪装成 MAC 原始命令。超过 ExHq 单页 1800 条时会自动分页。

扩展市场 `mac_quotes_list()` 也是组合接口：证券代码来自标准 Ex 品种目录，再按每批 80 个代码调用
MAC `0x122B`。MAC `0x2562` 的 `mac_goods_list()` 仅由 A 股 MAC 客户端原样公开，不作为扩展市场
品种目录，也不在 Ex 客户端上注册。

标准 `fund_flows()` 使用的是 `0x054C` 当日资金扩展，不等同于
`mac_capital_flow()`。当前尚未确认标准行情服务器存在可用的历史日线资金流原始命令；
Category 22 / `0x052D` 在实测节点上只返回空响应，因此 next 引擎不对外暴露该接口。
