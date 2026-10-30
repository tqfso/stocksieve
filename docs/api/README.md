# StockSieve V1 API 接口文档

状态：设计稿，接口尚未实现。本文档集是前后端联调契约，覆盖[需求说明](../requirements.md)及当前前端页面。业务策略遵循[选股策略设计](../product/strategies/README.md)。

## 阅读入口

| 文档 | 内容 |
| --- | --- |
| [通用约定](common/conventions.md) | 请求与响应、鉴权、空值及分页 |
| [数据快照](common/snapshots.md) | 数据日期、复权口径和跨页面数据一致性 |
| [异步任务](common/tasks.md) | 状态转换、轮询、重复提交与重试 |
| [同步规则](common/sync-policy.md) | 定时调度、板块预检与延迟重试 |
| [错误处理](common/errors.md) | HTTP 状态、业务错误码与界面反馈 |
| [前端对接说明](integration.md) | 接口调用顺序及模拟模型的转换 |

每个接口单独一个 Markdown 文件，README 仅作索引。接口按登录权限、行情选股、AI 分析和管理员功能归档；点击下表的用途可进入对应接口文档。响应示例中的 JSON 均遵循公共响应结构。

## 接口总览

路径均相对于 `/api/v1`；“登录用户”包含管理员与普通用户。

| 方法 | 路径 | 用途 | 权限 |
| --- | --- | --- | --- |
| POST | `/auth/login` | [登录](auth/login.md) | 无需登录 |
| GET | `/auth/me` | [当前用户](auth/me.md) | 登录用户 |
| POST | `/auth/logout` | [退出当前会话](auth/logout.md) | 登录用户 |
| PUT | `/auth/password` | [修改自身密码](auth/password.md) | 管理员 |
| GET | `/market/status` | [数据日期、覆盖范围、可用渠道](market/status.md) | 登录用户 |
| GET | `/catalog/filters` | [行业、概念及上市板块选项](market/filters.md) | 登录用户 |
| GET | `/strategies` | [策略说明](market/strategies.md) | 登录用户 |
| GET | `/opportunities` | [机会筛选与汇总](market/opportunities.md) | 登录用户 |
| GET | `/stocks/{stockId}` | [个股、行情与策略信号](market/stocks.md) | 登录用户 |
| GET | `/stocks/{stockId}/candles` | [日 / 周 / 年 K 线](market/candles.md) | 登录用户 |
| POST | `/stocks/{stockId}/analyses` | [发起分析](analysis/create.md) | 登录用户 |
| GET | `/analyses` | [本人分析历史](analysis/list.md) | 登录用户 |
| GET | `/analyses/{analysisId}` | [分析状态和报告](analysis/detail.md) | 报告所属用户 |
| POST | `/admin/sync-jobs` | [发起数据任务](admin/sync-jobs/create.md) | 管理员 |
| GET | `/admin/sync-jobs` | [任务记录](admin/sync-jobs/list.md) | 管理员 |
| GET | `/admin/sync-jobs/{jobId}` | [同步状态](admin/sync-jobs/detail.md) | 管理员 |
| GET | `/admin/sync-jobs/{jobId}/logs` | [任务日志](admin/sync-jobs/logs.md) | 管理员 |
| GET | `/admin/users` | [用户列表](admin/users/list.md) | 管理员 |
| POST | `/admin/users` | [新增普通用户](admin/users/create.md) | 管理员 |
