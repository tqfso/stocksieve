# 生成报告 `POST /stocks/{stockId}/analyses`

[模块目录](README.md) · [接口总览](../README.md)

登录用户可对任意被所选快照覆盖的股票生成报告，不要求入选策略。需 `Idempotency-Key`。

```json
{ "snapshotId": "snapshot_example" }
```

`snapshotId`可选，省略使用最新已发布行情；受理后固定。请求不再接受`kind`、`strategy`或模拟场景参数。

成功`202`，`Location`指向任务详情；`data`含`id`、`stockId`、`kind=report`、`strategy=null`、`snapshotId`、`dataDate`、`simulated`、`status`、`stage`、`progress`、各项任务时间、`error`和`report`。受理时为`queued/preparing`，`report=null`。

同一用户、股票、快照存在进行中报告时，新key返回`409 ANALYSIS_IN_PROGRESS`，`error.details.analysisId`指向原任务。网络超时重发沿用原key；主动重新生成或重试失败任务使用新key，已有报告保留。

没有已发布行情返回`409 DATA_NOT_READY`，股票不在快照内返回`404 STOCK_NOT_FOUND`。财报、筹码或技术窗口不足时仍可返回说明限制的报告，不补造价格或事实。

当前`simulated=true`，任务和报告保存模拟标识；模式变化不改写已受理输入。其他时间和正文约定见[详情](detail.md)。
