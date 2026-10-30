# 异步任务与重复提交

[接口总览](../README.md)

AI 分析、数据同步提交后返回 `202` 和任务资源，响应头 `Location` 指向任务详情。状态转换为：

```text
queued → running → succeeded
   └──────────────→ failed
```

`running` 也可进入 `failed`。`progress` 为 `0～100` 的整数；成功时为 `100`，失败保留最后进度。任务包含可空的 `startedAt`、`finishedAt`、`error`；失败时 `error` 必须包含可读原因与错误码。

收盘同步允许 `running → queued → running` 以等待板块数据就绪，仍为同一任务；字段及重试规则见[同步规则](sync-policy.md)。AI 分析不使用此等待流程。

- 通过接口提交的两类任务均要求 `Idempotency-Key` 请求头，值为客户端生成的 8～128 位唯一字符串，建议 UUID。
- 同一用户、方法和路径下，相同 key 与相同请求体在 24 小时内返回同一任务及原受理业务数据，requestId 使用本次请求值；同 key 换请求体返回 `409 IDEMPOTENCY_CONFLICT`。
- 请求超时后的重发沿用原 key；已失败任务的主动重试生成新 key，创建新任务并保留旧记录。V1 无任务取消接口。
- 前端可每 2 秒查询一次任务，终态停止轮询；网络异常时退避重试。刷新、退出或登录过期不会取消后端已受理任务，重新登录后可继续查询。
