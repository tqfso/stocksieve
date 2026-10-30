# 发起任务 `POST /admin/sync-jobs`

[模块目录](README.md) · [接口总览](../../README.md) · [通用约定](../../common/conventions.md)

权限：管理员。

仅管理员手动调用，需 `Idempotency-Key`。初始化请求：

```json
{ "kind": "initialize", "source": "sina" }
```

行业、题材同步请求：

```json
{ "kind": "catalog", "source": "catalog_provider" }
```

收盘同步请求：

```json
{ "kind": "daily", "source": "sina", "dataDate": "2026-10-28" }
```

- `kind` 取 `initialize`（股票基础信息）、`catalog`（行业、题材及股票归属）、`daily`（收盘行情与策略）。`source` 取[数据状态](../../market/status.md)返回的可用且支持对应任务的渠道 ID，两者必填；`catalog_provider` 仅为分类渠道示例。
- `initialize`、`catalog` 不接受 `dataDate`，可在非交易日执行；`catalog` 需先初始化股票，且只更新分类。`daily` 省略日期时使用北京时间当天，也可指定历史交易日。
- `catalog`、`daily` 未初始化返回 `409 DATA_NOT_INITIALIZED`；分类未同步不阻塞收盘任务。收盘任务提前触发限当天收盘后，仍需预检；非交易日返回 `400 NOT_TRADING_DAY`，未收盘或未来日期返回 `409 MARKET_NOT_CLOSED`。
- 来源不可用或不支持该任务返回 `400 SOURCE_UNAVAILABLE`，不自动换源。已有未完成同步任务时返回 `409 SYNC_IN_PROGRESS`，`error.details.jobId` 指向该任务。

成功 `202`，`Location` 指向[任务详情](detail.md)，响应示例：

```json
{
  "data": {
    "id": "job_example", "kind": "daily", "source": "sina", "dataDate": "2026-10-28",
    "trigger": "manual", "scheduledFor": null,
    "status": "queued", "progress": 0,
    "waitReason": null, "nextRetryAt": null, "retryDeadlineAt": null, "retryCount": 0, "precheck": null,
    "createdAt": "2026-10-29T18:30:00+08:00", "startedAt": null, "finishedAt": null,
    "result": null, "error": null
  },
  "requestId": "req_example"
}
```

`trigger` 为 `manual` 或 `scheduled`，由服务端确定；`scheduledFor` 为定时任务的计划触发时间，手动时为 null。`initialize`、`catalog` 仅手动执行，`dataDate` 为 null。两类任务互不修改对方数据，不删除快照、用户或分析结果；同日手动重同步收盘行情可生成新快照。

分类更新频率见[数据管理](../../../requirements.md#2-数据管理)。分类刷新不改写已发布快照，收盘同步复用已保存的分类关系。两类基础任务成功后分别更新[数据状态](../../market/status.md)中的最近成功时间；失败保留原数据和时间。

[同步调度、预检与只读字段](../../common/sync-policy.md)
