# StockSieve 后端

Python 3.12、aiohttp、Pydantic、SQLAlchemy；依赖和锁文件由 uv 管理，当前锁定 Linux x86_64 部署环境。生产使用现有 PostgreSQL；测试支持独立 SQLite 或 PostgreSQL 临时 schema，不会自动重建生产表。

## 当前状态

| 模块 | 已实现 | 仍需完成 |
| --- | --- | --- |
| 账号与接口 | 登录、会话撤销、改密、用户管理、参数/权限/错误响应；真实 PostgreSQL＋HTTP 回归 | Docker 服务验收 |
| 同步与调度 | 单源能力接口、去重/幂等、预检、持久化队列、租约、分批恢复、18点调度 | 真实免费供应商适配及覆盖验证 |
| 策略和查询 | 五类策略、前复权、日/周/年 K 线、快照发布/冻结、筛选与汇总 | 全市场数据与容量测试 |
| AI 分析 | 默认模拟报告、单个分析技能、资料工具、TTM/PE/筹码计算、来源/减持证据校验、报告存储和权限；已安装SDK的技能加载与协议配置测试 | 真实模型协议实测、免费检索渠道、财报采集及PDF文本读取 |
| 部署 | Dockerfile、Compose、运行材料准备、数据库预检与HTTP验收脚本；已确认本机 ubuntu:24.04 | 实际构建并启动容器 |

已通过 uv 安装并锁定 `psycopg 3.3.6`、`deepagents 0.7.32`、`langchain-openai 1.7.3`。真实 PostgreSQL 连接和现有17张表结构检查通过；智能体测试验证实际SDK构图、完整技能正文加载及双协议配置，不请求真实模型。Docker Compose 配置校验通过；按用户最新安排暂缓构建和启动容器。

Sina、Baostock、东方财富仍为候选，未注册为生产可用适配器；配置开关不会把未验证渠道强行变为可用。模拟行情渠道只存在于测试目录，由测试显式注入，生产入口不加载模拟行情。

## 离线测试

在项目根目录执行：

```bash
uv sync --project backend --frozen
uv run --project backend --frozen pytest backend/tests backend/tools/tests -q
uv run --project backend --frozen python backend/run.py --help
```

接口测试执行真实路由和中间件，无需监听端口；覆盖角色与报告归属、幂等重放、租约抢占、源等待与超时、分批恢复、快照不可变、策略边界、周期聚合、来源校验及请求限流。未用离线通过代替 PostgreSQL 或外部SDK测试。

## 真实数据库与HTTP自测

```bash
uv run --project backend --frozen pytest backend/tests --postgres --http -q
```

`--postgres` 使用项目现有数据库连接，在随机的 `stocksieve_test_` schema 内运行与业务库相同的初始化 SQL 和完整约束，结束后只清理本次创建的 schema；不会修改 `public` 或在业务表写入模拟行情。也可用 `--postgres-url-env` 指定测试数据库URL的环境变量名称。

`--http` 启动仅监听本机随机端口的临时测试服务，通过实际 TCP/HTTP 发送接口请求。测试中的供应商和模型仍使用测试输入，验证数据库与HTTP链路不代表真实行情源或模型已经验收；Docker正式服务的HTTP检查另见部署说明。

两种模式均为显式启用，缺驱动、数据库权限或监听权限时失败，不静默退回 SQLite。

2026-10-31：本地完整回归76项通过；真实 PostgreSQL＋HTTP 模式61项通过（含4项本地智能体SDK测试），覆盖默认模拟、三类报告、历史标识与模式切换。测试后核对17张业务表结构正常、临时 schema 为0；`public` 的用户、股票、任务、快照、日线、策略信号及分析结果均为0行。业务库没有模拟数据，真实股票初始化仍待免费渠道接入。

## 运行与配置

生产前提及单机命令见[后端部署](../docs/deployment/backend.md)。应用只读取服务器配置，接口不提供修改模型凭据或任意数据源地址的功能。开发设计见[后端概要](../docs/design/backend.md)，产品内容见 [AI 分析设计](../docs/product/ai-analysis-design.md)。

AI 当前默认 `STOCKSIEVE_AI_MODE=mock`，无需模型配置，不调用模型或联网检索；三类分析复用冻结快照、实际计算及任务/历史接口，任务和报告均返回 `simulated=true`，报告标题和摘要标明模拟。缺失财报可生成模拟基本面报告，但不补造财务或减持事实；缺少核心行情仍拒绝受理。后续接入真实模型时改为 `live` 并填写[模型配置](.env.example)，真实模式配置不全会明确不可用，不自动退回模拟。

部署统一使用 Docker Compose，基于已确认在本机存在的 `ubuntu:24.04`。旧 systemd 草案已移除；当前已完成配置准备，容器运行兼容性与实际构建、启动仍待验证。

本轮未修改初始化 SQL 或现有业务表；真实数据库测试仅创建并清理隔离 schema。所需任务阶段与冻结资料使用已有 JSON 字段和业务表保存。后续确需调整 SQL 时，按项目约定同步直接更新现有库。
