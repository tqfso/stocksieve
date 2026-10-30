# 发起分析 `POST /stocks/{stockId}/analyses`

[模块目录](README.md) · [接口总览](../README.md) · [通用约定](../common/conventions.md)

权限：登录用户。

需 `Idempotency-Key`。请求：

```json
{ "kind": "fundamental", "snapshotId": "snapshot_example" }
```

`kind` 取 `fundamental`、`chips`、`trade`，必填；`snapshotId` 可选，受理时解析为确定快照并固定到任务。买卖条件分析（`kind=trade`）还需传 `strategy`，取 `short`、`swing`、`long`、`trend_wait` 或 `base_wait`，对应详情中选中的策略；该策略未在所选快照入选时返回 `409 ANALYSIS_DATA_UNAVAILABLE`。其他类型无需传策略。

成功 `202`：

```json
{
  "data": {
    "id": "analysis_example", "stockId": "SZSE.300750", "kind": "fundamental",
    "strategy": null,
    "snapshotId": "snapshot_example", "dataDate": "2026-10-28",
    "status": "queued", "progress": 0,
    "createdAt": "2026-10-29T18:30:00+08:00", "startedAt": null, "finishedAt": null,
    "error": null, "report": null
  },
  "requestId": "req_example"
}
```

同一用户、股票、快照、分析类型及策略已有非终态任务时，新 key 提交返回 `409 ANALYSIS_IN_PROGRESS`，`error.details.analysisId` 指向已有任务。非买卖条件任务的 `strategy` 为 `null`，买卖条件任务返回实际策略。历史成功报告不会因重新生成而覆盖。受理时没有足够日线数据或该股票不支持指定分析时，返回 `409 ANALYSIS_DATA_UNAVAILABLE` 并说明原因。
