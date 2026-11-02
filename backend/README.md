# StockSieve 后端

Python3.12、aiohttp、SQLAlchemy、PostgreSQL，Python依赖和锁文件统一由uv管理。当前先上线沪深四板块，北交所后续补齐；AI采用后端模拟模式。

源码按`identity`、`market`、`ingestion`、`jobs`、`analysis`业务模块分包，核心模型/规则在`domain`，用例在`application`，供应商和智能体实现在模块内`infrastructure`；HTTP入口、共享持久化及启动组装独立。结构与依赖约束见[后端概要](../docs/design/backend.md#2-模块职责)，CLI命令保持兼容。

## 当前状态

- PostgreSQL、API、worker、scheduler统一由backend/compose.yaml管理；后端基础镜像为本机ubuntu:24.04，API同源提供编译后的真实前端。
- 登录、会话撤销、用户管理、持久化任务、策略与快照、查询、图表、报告和权限已实现。页面不再使用浏览器模拟业务层。
- 2026-11-01按用户要求清理了旧容器、数据库和状态卷，并从空库重新部署；旧库与缓存已归档到私有备份目录，仅恢复原管理员账号，源频控账本完整保留。
- 已重新发布2026-10-30快照：5223只股票的行业和市值完整，当前187只候选股均有题材，整体题材覆盖仍为partial。短线32、波段4、长线150、趋势埋伏0、低位筑底9。先发布近期策略、再后台补齐2500个交易日（约十年），回补期间保留已发布结果。
- 免费题材使用百度股市通，每批20只、每轮最多400只，优先当前候选股并保留原行业；未取得的股票归属按partial明确提示，可继续分轮补齐。
- 正式渠道明示组合：Baostock行情/日历/行业/除权参考价，腾讯市值及新增编码的明确板块标签。价格与复权不跨上游混合；缺失资料保留未知。
- 日常更新与历史回补可同时排队，日常优先；运行中的回补在保存批次后主动让位。初始化SQL与现有PG的同步去重索引已直接对齐，保留已有快照和任务。

最新数据源证据见[验证记录](../docs/research/data-sources.md)，模块设计见[后端概要](../docs/design/backend.md)。报告文档导出和真实AI服务属于后续扩展。

## 运行与配置

正式启动及维护见[Docker部署](../docs/deployment/backend.md)。服务器与数据库配置统一放在`backend/.env`，环境变量优先。[示例](.env.example)不包含真实凭据。

当前`STOCKSIEVE_AI_MODE=mock`，无需模型配置，不调用真实模型或联网检索。任务和报告均返回`simulated=true`，标题与摘要明确标记；图表与参考价仍用实际输入计算。缺财报可生成说明缺失的模拟报告，缺少核心行情仍拒绝受理。未来改为`live`并填写模型参数；真实模式不可用时不自动回退模拟。

历史统一使用`STOCKSIEVE_HISTORY_TRADING_DAYS=150`与`STOCKSIEVE_HISTORY_MAX_TRADING_DAYS=2500`，分别控制首次策略窗口和后台回补/保留上限；0上限表示不限。上市较晚的股票从实际可得行情开始，旧快照和报告数据保留。配置示例与生效方式见部署说明。

正式源默认共享1秒请求间隔、项目预算1000次/日，其中最多100次为日常更新预留；这些是项目配置，不是数据源公布的硬限制。日常任务优先于历史回补，回补不禁用管理页的日常更新入口。共用原访问账本、次数与冷却，缓存命中不重复请求，明确拒绝访问时暂停；正常成功流中断最多短退避3次。

## 测试

在仓库根目录执行：

```bash
uv sync --project backend --frozen
uv run --project backend --frozen pytest backend/tests backend/tools/tests -q
uv run --project backend --frozen pytest backend/tests backend/tools/tests --postgres --http --browser -q
```

默认模式使用独立SQLite；`--postgres`使用现有连接，在随机`stocksieve_test_`schema中执行完整SQL约束并自动清理，不向public写模拟行情。`--http`通过本机随机端口发送真实请求；`--browser`使用隔离浏览器上下文验证真实前端，需先构建前端。供应商与模型夹具仍是测试输入，与正式数据源验收分开记录。

近期发布、历史回补、日常任务优先与主动让位、预算预留、定时任务、旧快照保持和旧链接恢复均已通过相关PostgreSQL/HTTP/浏览器验收。正式HTTP已实测回补期间提交并完成日常更新，回补随后续跑；完整历史进度以数据管理为准。

VS Code从仓库根打开时，[工作区配置](../.vscode/settings.json)指定`backend/src`和uv解释器；旧诊断可重新选择`backend/.venv/bin/python`并执行`Developer: Reload Window`。
