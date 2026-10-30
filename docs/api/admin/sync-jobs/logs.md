# 任务日志 `GET /admin/sync-jobs/{jobId}/logs`

[模块目录](README.md) · [接口总览](../../README.md) · [通用约定](../../common/conventions.md)

权限：管理员。

可选 `after` 游标、`limit`（1～200，默认100）。成功 `200`，响应示例：

```json
{
  "data": {
    "items": [{ "id": "log_example", "time": "2026-10-29T18:30:05+08:00", "level": "info", "message": "已开始读取股票基础信息。" }],
    "hasMore": false, "nextCursor": "log_example"
  },
  "requestId": "req_example"
}
```

日志升序返回，不含 `after` 自身；`level` 为 `info`、`warning`、`error`。`nextCursor` 为最后一条 ID，无新记录时保留 `after`，首次无记录为 null。`hasMore` 仅表示当前剩余日志，任务未结束时可继续轮询。无效或不属于该任务的游标返回 `400 INVALID_CURSOR`。

[同步调度、预检与只读字段](../../common/sync-policy.md)
