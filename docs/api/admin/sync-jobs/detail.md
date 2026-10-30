# 任务详情 `GET /admin/sync-jobs/{jobId}`

[模块目录](README.md) · [接口总览](../../README.md) · [通用约定](../../common/conventions.md)

权限：管理员。

成功 `200`，`data` 为完整任务；`data.result` 尚无结果时为 null。完成后的响应示例：

```json
{
  "data": {
    "id": "job_example", "kind": "daily", "source": "sina", "dataDate": "2026-10-28",
    "trigger": "manual", "scheduledFor": null,
    "status": "succeeded", "progress": 100,
    "waitReason": null, "nextRetryAt": null, "retryDeadlineAt": null, "retryCount": 0,
    "precheck": {
      "checkedAt": "2026-10-29T18:30:02+08:00",
      "boards": [
        { "board": "SSE_MAIN", "status": "ready", "latestDataDate": "2026-10-28", "message": null },
        { "board": "SZSE_MAIN", "status": "ready", "latestDataDate": "2026-10-28", "message": null },
        { "board": "CHINEXT", "status": "ready", "latestDataDate": "2026-10-28", "message": null },
        { "board": "STAR", "status": "ready", "latestDataDate": "2026-10-28", "message": null },
        { "board": "BSE", "status": "ready", "latestDataDate": "2026-10-28", "message": null }
      ]
    },
    "createdAt": "2026-10-29T18:30:00+08:00", "startedAt": "2026-10-29T18:30:01+08:00", "finishedAt": "2026-10-29T18:31:00+08:00",
    "result": {
      "outcome": "updated",
      "totalCount": 5348, "processedCount": 5348,
      "successCount": 5320, "skippedCount": 28, "failedCount": 0,
      "publishedSnapshotId": "snapshot_example"
    },
    "error": null
  },
  "requestId": "req_example"
}
```

统计按股票计数，`processedCount = successCount + skippedCount + failedCount <= totalCount`，成功时处理数等于目标数；`skippedCount` 仅指按规则无需处理的股票，不包括读取失败或策略剔除。

| outcome | 结果 | publishedSnapshotId |
| --- | --- | --- |
| initialized | 初始化完成 | null |
| updated | 数据与策略结果已发布 | 新快照 ID |
| market_closed | 定时检查确认休市，跳过 | null |
| already_synced | 当天已完整同步，跳过 | 已有快照 ID |

后两项均为 `succeeded`、`progress=100`、`error=null`，股票计数为 0，不产生新快照。正式同步读取或校验失败时为 `failed`；已知统计可保留，`outcome` 和 `publishedSnapshotId` 为 null。预检中的可恢复异常按等待规则处理。

任务失败通过 `error.code` 和 `message` 说明，查询本身仍可返回 `200`。错误码包括 `DATA_READY_TIMEOUT`、`BOARD_DATA_UNSUPPORTED`、`SOURCE_TIMEOUT`、`DATA_NOT_AVAILABLE`、`DATA_INCOMPLETE`、`TRADING_CALENDAR_UNAVAILABLE`、`STRATEGY_CALCULATION_FAILED`；不能把数据未取得当作休市，也不暴露内部堆栈或凭据。

[同步调度、预检与只读字段](../../common/sync-policy.md)
