# 继续失败任务 `POST /admin/sync-jobs/{jobId}/retry`

[模块目录](README.md) · [任务详情](detail.md)

权限：管理员。提供新的`Idempotency-Key`，请求体为`{}`。

成功返回`202`和原任务，`Location`仍指向原ID。任务从`failed`回到`queued`，保留已完成股票、快照、来源、日期、历史配置和日志；清除本次失败状态后，从下一只未完成股票继续。没有股票检查点的任务继续其资料准备批次。供应商访问预算和冷却不重置。

同一key重发返回原受理结果；任务完成后不能重试。需要不同日期、渠道或重新采集时，使用[发起任务](create.md)。

| 错误 | 含义 |
| --- | --- |
| `404 SYNC_JOB_NOT_FOUND` | 任务不存在 |
| `409 SYNC_NOT_FAILED` | 当前任务未失败 |
| `409 SYNC_IN_PROGRESS` | 存在冲突的执行任务，details.jobId指向该任务 |
| `409 SYNC_CHECKPOINT_INVALID` | 检查点不完整，未清空数据或重新开始 |
