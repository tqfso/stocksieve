# 管理员数据同步

[接口总览](../README.md) · [通用约定](../common/conventions.md) · [异步任务约定](../common/tasks.md)

## 发起任务 `POST /admin/sync-jobs`

需 `Idempotency-Key`。初始化请求：

```json
{ "kind": "initialize", "source": "sina" }
```

收盘同步请求：

```json
{ "kind": "daily", "source": "sina", "dataDate": "2026-10-28" }
```

- `kind`、`source` 必填；初始化请求不接受 `dataDate`。收盘同步 `dataDate` 可选，默认截至请求时最近已收盘的交易日，受理后固定到任务。
- 未初始化先同步返回 `409 DATA_NOT_INITIALIZED`；非交易日返回 `400 NOT_TRADING_DAY`；尚未收盘或未来日期返回 `409 MARKET_NOT_CLOSED`。
- 不可用渠道或渠道不支持指定任务返回 `400 SOURCE_UNAVAILABLE`，不自动切换渠道。
- 全系统最多一个非终态同步任务；已有任务时新 key 返回 `409 SYNC_IN_PROGRESS`，`error.details.jobId` 指向该任务。

成功 `202`，`Location` 指向 `/api/v1/admin/sync-jobs/{jobId}`；任务结构如下：

```json
{
  "id": "job_example", "kind": "daily", "source": "sina", "dataDate": "2026-10-28",
  "status": "queued", "progress": 0,
  "createdAt": "2026-10-29T18:30:00+08:00", "startedAt": null, "finishedAt": null,
  "result": null, "error": null
}
```

初始化任务 `dataDate` 为 `null`。重新初始化不清除已发布快照、用户或 AI 报告。相同交易日可重新同步，成功后发布新快照。

## 任务列表 `GET /admin/sync-jobs`

可选 `kind`、`status`、`page`、`pageSize`。成功 `200`，标准分页；每项为完整任务结构，按 `createdAt` 降序、相同时间按 `id` 降序。

## 任务详情 `GET /admin/sync-jobs/{jobId}`

成功 `200`，返回完整任务。成功时 `error: null`，`result` 为：

```json
{
  "totalCount": 5348,
  "processedCount": 5348,
  "successCount": 5320,
  "skippedCount": 28,
  "failedCount": 0,
  "publishedSnapshotId": "snapshot_example"
}
```

统计按股票计数，`totalCount` 是本次目标数量；同一股票计入其中一种处理结果，`processedCount = successCount + skippedCount + failedCount <= totalCount`。成功时处理数等于目标数，失败时允许尚有未处理股票。`skippedCount` 表示按明确规则无需处理的记录，不包含读取失败；策略剔除不等同于行情同步失败。

初始化成功时 `publishedSnapshotId: null`；收盘同步成功时该字段必填，代表数据及策略结果均已发布。部分来源读取失败或完整性校验失败时，任务为 `failed`，不发布快照；可在失败任务的 `result` 中返回已知处理统计，`publishedSnapshotId` 为 `null`。尚无结果时 `result: null`。

失败 `error` 包含例如 `SOURCE_TIMEOUT`、`DATA_INCOMPLETE`、`STRATEGY_CALCULATION_FAILED` 等业务错误码及可读说明，不暴露内部堆栈或凭据。

## 任务日志 `GET /admin/sync-jobs/{jobId}/logs`

可选 `after` 游标、`limit`（整数 `1～200`，默认 `100`）；首次省略 `after`，从最早日志开始。成功 `200`：

```json
{
  "items": [
    { "id": "log_example", "time": "2026-10-29T18:30:05+08:00", "level": "info", "message": "已开始读取股票基础信息。" }
  ],
  "hasMore": false,
  "nextCursor": "log_example"
}
```

日志按生成顺序升序返回，`after` 之后的记录不包含游标自身。`level` 为 `info`、`warning`、`error`。有记录时 `nextCursor` 为最后一条 ID，无新记录时保留传入 `after`，首次即无记录则为 `null`；`hasMore` 表示当前还有待读取日志，不表示任务是否完成。非终态任务即使暂无更多日志也可继续轮询。错误或不属于该任务的游标返回 `400 INVALID_CURSOR`。
