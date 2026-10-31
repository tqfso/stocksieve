# StockSieve 后端

Python 3.12、aiohttp、Pydantic、SQLAlchemy；依赖和锁文件由 uv 管理，当前锁定 Linux x86_64 部署环境。生产使用现有 PostgreSQL，离线测试使用独立 SQLite，并校验外键和事务；不会自动重建生产表。

## 当前状态

| 模块 | 已实现 | 仍需完成 |
| --- | --- | --- |
| 账号与接口 | 登录、会话撤销、改密、用户管理、参数/权限/错误响应 | 真实 PostgreSQL 联调 |
| 同步与调度 | 单源能力接口、去重/幂等、预检、持久化队列、租约、分批恢复、18点调度 | 真实免费供应商适配及覆盖验证 |
| 策略和查询 | 五类策略、前复权、日/周/年 K 线、快照发布/冻结、筛选与汇总 | 全市场数据与容量测试 |
| AI 分析 | 单个分析技能、资料工具、TTM/PE/筹码计算、来源/减持证据校验、报告存储和权限 | 智能体SDK安装与两种协议实测、免费检索渠道、财报采集及PDF文本读取 |
| 部署 | 运行入口、健康检查、管理员初始化命令、systemd配置 | 安装并验证生产依赖、启用真实进程 |

当前环境无法访问 Docker/数据库或联网，自动审批服务返回503。`psycopg`、`deepagents`、`langchain-openai` 尚未安装及锁定，不能把这部分算作已完成联调。现有核心依赖已通过缓存离线安装并生成标准 PyPI 锁文件。

Sina、Baostock、东方财富仍为候选，未注册为生产可用适配器；配置开关不会把未验证渠道强行变为可用。测试渠道只存在于测试目录，由测试显式注入，生产入口不加载模拟行情或模拟模型。

## 离线测试

在项目根目录执行：

```bash
uv sync --project backend --frozen
uv run --project backend --frozen pytest backend/tests backend/tools/tests -q
uv run --project backend --frozen python backend/run.py --help
```

接口测试执行真实路由和中间件，无需监听端口；覆盖角色与报告归属、幂等重放、租约抢占、源等待与超时、分批恢复、快照不可变、策略边界、周期聚合、来源校验及请求限流。未用离线通过代替 PostgreSQL 或外部SDK测试。

## 运行与配置

生产前提及单机命令见[后端部署](../docs/deployment/backend.md)。应用只读取服务器配置，接口不提供修改模型凭据或任意数据源地址的功能。开发设计见[后端概要](../docs/design/backend.md)，产品内容见 [AI 分析设计](../docs/product/ai-analysis-design.md)。

本轮未修改初始化 SQL 或现有 PostgreSQL；所需的任务阶段与冻结资料使用已有 JSON 字段和业务表保存。后续确需调整 SQL 时，按项目约定同步直接更新现有库。
