# 错误码与前端处理

[接口总览](../README.md)

| HTTP 状态 | 业务错误码示例 | 前端处理 |
| --- | --- | --- |
| 400 | `INVALID_ARGUMENT`、`FILTER_UNAVAILABLE`、`CURRENT_PASSWORD_INVALID`、`SOURCE_UNAVAILABLE`、`NOT_TRADING_DAY`、`INVALID_CURSOR` | 提示字段或操作原因，保留用户输入 |
| 401 | `UNAUTHENTICATED`、`TOKEN_EXPIRED`、`INVALID_CREDENTIALS` | 登录失败停留登录页；其他接口清理会话并回到登录页 |
| 403 | `FORBIDDEN` | 提示无权限，不展示管理功能 |
| 404 | `STOCK_NOT_FOUND`、`SNAPSHOT_NOT_FOUND`、`ANALYSIS_NOT_FOUND`、`SYNC_JOB_NOT_FOUND` | 显示资源不存在，并提供返回入口 |
| 409 | `DATA_NOT_READY`、`DATA_NOT_INITIALIZED`、`MARKET_NOT_CLOSED`、`USERNAME_EXISTS`、`SYNC_IN_PROGRESS`、`ANALYSIS_IN_PROGRESS`、`ANALYSIS_DATA_UNAVAILABLE`、`IDEMPOTENCY_CONFLICT` | 根据错误码提示前置条件、已有任务或重复提交原因 |
| 429 | `RATE_LIMITED` | 遵循响应头 `Retry-After`（秒）等待，不连续重发 |
| 500 / 503 | `INTERNAL_ERROR` / `SERVICE_UNAVAILABLE` | 展示可重试提示及 requestId，不把错误当作空列表 |

参数格式和枚举错误使用 `400`；资源 ID 合法但不存在使用 `404`。列表筛选的未知分类 ID 使用 `400 INVALID_ARGUMENT`。

异步任务自身失败通过详情中的 `data.status=failed` 与 `data.error` 展示，任务查询仍可成功返回 `200`。重试创建新任务；只有请求失败的重发才复用原 `Idempotency-Key`。
