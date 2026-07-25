# Next SDK 当前验收结论

> 本页替代早期 Week 10 的阶段性 No-Go 结论；周级名称仅为保留原文档链接。

## 结论

- 独立 `mootdx_next` SDK：`Go`
- 标准市场 `Quotes.factory()` 默认切换到 next：`Go`
- 旧标准市场 API 薄适配：`Go`
- 本地标准/扩展 Reader：`Go`
- 在线扩展市场 EX：`Unsupported`
- 旧 GP socket 财务下载：`Unsupported`

## 已验收范围

- Raw：`SyncClient`、`AsyncClient`
- Pandas：`PandasClient`、`AsyncPandasClient`
- 标准市场行情、K 线、指数、分时、逐笔、财务、除权除息、F10 和板块接口
- `get_k_data`、`k`、`ohlc` 各自保留原公开名字和签名
- 本地日线、分钟线、扩展市场、板块和自定义板块 Reader
- 官方 HTTPS 财务目录、下载、完整性校验和 DAT/ZIP 内存解析
- 进程级、线程安全、10 分钟 TTL 的候选 IP 注册表

## 解耦门禁

`mootdx_next` 生产树不得静态或动态导入 `mootdx`。测试同时使用 AST 扫描和屏蔽旧命名空间的子进程
全模块导入，确保 next-only 安装不依赖 `tdxpy`。旧 `mootdx`、CLI 和兼容门面可以单向调用 next。

两个命名空间继续由同一个 `mootdx` wheel 发布，因此已有用户不需要安装第二个发行包。

## 兼容性判定

固定基线为：

- Python 3.11
- `mootdx==0.11.7`
- `tdxpy==0.2.7`

测试默认要求 next 与固定原版 artifact 精确一致。只有同时满足以下条件的差异才允许：

1. 已确认是原版缺陷；
2. 有确定性回归测试；
3. API、case 和结果路径已经登记到 deviation registry。

## 验证分层

- 普通 CI：单元测试、公共 API/参数矩阵、依赖边界和 corpus replay
- next-only：不安装 legacy extra 的独立安装验证
- opt-in live：真实行情、F10、财务和节点能力矩阵

实时 `transaction()` 只在交易时段运行；历史 `transactions()` 不受当前交易时段限制。F10/F10C 使用
`600036`，指数使用明确市场参数或 `sh000001`，避免 `000001` 的市场歧义。
