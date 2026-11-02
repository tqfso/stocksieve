# 发起分析 `POST /stocks/{stockId}/analyses`

[模块目录](README.md) · [接口总览](../README.md) · [通用约定](../common/conventions.md)

权限：登录用户。

需 `Idempotency-Key`。请求：

```json
{ "kind": "fundamental", "snapshotId": "snapshot_example" }
```

`kind` 取 `fundamental`、`chips`、`trade`，必填；`snapshotId` 可选，受理时固定实际行情版本。买卖建议省略`strategy`或传`null`时为通用分析，不要求入选策略；指定`short`、`swing`、`long`、`trend_wait`或`base_wait`时需该快照已入选，否则返回`409 ANALYSIS_DATA_UNAVAILABLE`。其他类型不接受策略字段。

所有已覆盖的股票都可发起分析，不要求先入选五类策略。通用买卖建议以同步行情为核心，资料不足时可以返回无价位的等待报告；筹码缺核心数据则拒绝。当前基本面与买卖建议模拟报告可说明资料缺失，不补造财报或价格。尚无发布行情版本时返回`DATA_NOT_READY`，股票未被该版本覆盖时返回`STOCK_NOT_FOUND`。规则见 [AI 分析设计](../../product/ai-analysis-design.md)。

成功 `202`：

```json
{
  "data": {
    "id": "analysis_example", "stockId": "SZSE.300750", "kind": "fundamental",
    "strategy": null, "simulated": false,
    "snapshotId": "snapshot_example", "dataDate": "2026-10-28",
    "status": "queued", "stage": "preparing", "progress": 0,
    "createdAt": "2026-10-29T18:30:00+08:00", "startedAt": null, "finishedAt": null,
    "error": null, "report": null
  },
  "requestId": "req_example"
}
```

同一用户、股票、快照、分析类型及策略已有非终态任务时，新 key 提交返回 `409 ANALYSIS_IN_PROGRESS`，`error.details.analysisId` 指向已有任务。非买卖建议任务的 `strategy` 为 `null`，买卖建议任务返回实际策略。历史成功报告不会因重新生成而覆盖。受理时没有足够日线数据或该股票不支持指定分析时，返回 `409 ANALYSIS_DATA_UNAVAILABLE` 并说明原因。

`simulated` 表示本次任务是否生成模拟分析；当前阶段为 `true`。该标识从受理起固定，历史查询仍沿用原值。模拟分析也需已有行情快照，不能据此补造行情或财务数据。

客户端收到进行中冲突后查询已有任务；请求超时重发沿用原 `Idempotency-Key`，主动重新生成或重试失败任务使用新 key。业务阶段及报告时间字段见[分析详情](detail.md)。
