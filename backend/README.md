# StockSieve 后端

Python3.12、aiohttp、SQLAlchemy、PostgreSQL，Python依赖和锁文件统一由uv管理。当前先上线沪深四板块，北交所后续补齐；AI采用后端模拟模式。

源码按`identity`、`market`、`ingestion`、`jobs`、`analysis`业务模块分包，核心模型/规则在`domain`，用例在`application`，供应商和智能体实现在模块内`infrastructure`；HTTP入口、共享持久化及启动组装独立。结构与依赖约束见[后端概要](../docs/design/backend.md#2-模块职责)，CLI命令保持兼容。

## 当前状态

- API、worker、scheduler已通过Docker Compose在当前服务器启动，基础镜像为本机ubuntu:24.04；API同源提供编译后的真实前端。
- 登录、会话撤销、用户管理、持久化任务、策略与快照、查询、图表、报告和权限已实现。页面不再使用浏览器模拟业务层。
- 正式HTTP已完成初始化、行业同步和首轮发布：5223只股票，沪市主板1700、深市主板1494、创业板1411、科创板618；已保存779633条真实日线，覆盖2026-03-20至2026-10-30的150个交易日。
- 最新行情日为2026-10-30，短线32、波段4、长线150、趋势埋伏0、低位筑底9条信号；全部5223只股票的当日市值均经日期与收盘价交叉核对。
- 免费题材已接入百度股市通，每批20只、每轮最多400只，优先当前候选股并保留原行业。新快照已发布，首批400只股票、690个概念；当前187只候选股全部取得题材标签，其他股票按partial明确提示并可继续更新补齐。
- 正式渠道明示组合：Baostock行情/日历/行业/除权参考价，腾讯市值及新增编码的明确板块标签。价格与复权不跨上游混合；缺失资料保留未知。
- 现有库两条旧CHECK约束已直接对齐，解决行业任务和新增策略被旧约束拒绝的问题；未删除业务数据或生成增量SQL。

最新数据源证据见[验证记录](../docs/research/data-sources.md)，模块设计见[后端概要](../docs/design/backend.md)。报告文档导出和真实AI服务属于后续扩展。

## 运行与配置

正式启动及维护见[Docker部署](../docs/deployment/backend.md)。服务器配置可放在`backend/.env`，数据库默认读取既有`deploy/postgres/.env`；环境变量优先。[示例](.env.example)不包含真实凭据。

当前`STOCKSIEVE_AI_MODE=mock`，无需模型配置，不调用真实模型或联网检索。任务和报告均返回`simulated=true`，标题与摘要明确标记；图表与参考价仍用实际输入计算。缺财报可生成说明缺失的模拟报告，缺少核心行情仍拒绝受理。未来改为`live`并填写模型参数；真实模式不可用时不自动回退模拟。

当前正式源请求至少间隔10秒、全渠道每天最多400次，共用原探测账本、次数与冷却。缓存命中不重新请求；正常成功分块中断最多短退避3次，明确拒绝访问和上游等待指令继续严格执行。不要删除账本来恢复访问。

## 测试

在仓库根目录执行：

```bash
uv sync --project backend --frozen
uv run --project backend --frozen pytest backend/tests backend/tools/tests -q
uv run --project backend --frozen pytest backend/tests backend/tools/tests --postgres --http --browser -q
```

默认模式使用独立SQLite；`--postgres`使用现有连接，在随机`stocksieve_test_`schema中执行完整SQL约束并自动清理，不向public写模拟行情。`--http`通过本机随机端口发送真实请求；`--browser`使用隔离浏览器上下文验证真实前端，需先构建前端。供应商与模型夹具仍是测试输入，与正式数据源验收分开记录。

截至2026-11-01，分包后的完整PostgreSQL/HTTP/浏览器回归115项通过，另2项正式部署浏览器验收通过；包含领域依赖、应用端口和包导入/资源路径的架构约束。正式部署已复核登录、行情/K线、模拟报告及行业/题材输入定位和刷新恢复，截图在`backend/.state/concept-filter.png`。前端6组测试及生产构建此前通过；正式测试不改管理员口令。

VS Code从仓库根打开时，[工作区配置](../.vscode/settings.json)指定`backend/src`和uv解释器；旧诊断可重新选择`backend/.venv/bin/python`并执行`Developer: Reload Window`。
