# AI 分析与报告

[接口总览](../README.md) · [通用约定](../common/conventions.md) · [异步任务约定](../common/tasks.md)

## 发起分析 `POST /stocks/{stockId}/analyses`

需 `Idempotency-Key`。请求：

```json
{ "kind": "fundamental", "snapshotId": "snapshot_example" }
```

`kind` 必填；`snapshotId` 可选，受理时解析为确定快照并固定到任务。成功 `202`：

```json
{
  "id": "analysis_example", "stockId": "SZSE.300750", "kind": "fundamental",
  "snapshotId": "snapshot_example", "dataDate": "2026-10-28",
  "status": "queued", "progress": 0,
  "createdAt": "2026-10-29T18:30:00+08:00", "startedAt": null, "finishedAt": null,
  "error": null, "report": null
}
```

同一用户、股票、快照和分析类型已有非终态任务时，新 key 提交返回 `409 ANALYSIS_IN_PROGRESS`，`error.details.analysisId` 指向已有任务。历史成功报告不会因重新生成而覆盖。受理时没有足够日线数据或该股票不支持指定分析时，返回 `409 ANALYSIS_DATA_UNAVAILABLE` 并说明原因。

## 分析历史 `GET /analyses`

查询参数：可选 `stockId`、`kind`、`snapshotId`、`status`、`page`、`pageSize`。仅返回当前用户自己的记录，按 `createdAt` 降序、相同时间按 `id` 降序。

成功 `200`，标准分页；每项字段与发起分析响应相同，但列表省略 `report`，查看内容使用详情接口。报告历史下拉框可传 `stockId`、`kind`、`status=succeeded`；如仅看当前快照，再传 `snapshotId`。

## 分析详情 `GET /analyses/{analysisId}`

成功 `200`，返回完整任务。仅所属用户可访问，管理员也不能查看他人报告；无权访问与不存在均返回 `404 ANALYSIS_NOT_FOUND`。

非成功任务 `report: null`；失败时例如：

```json
{ "code": "ANALYSIS_TIMEOUT", "message": "分析超时，请重新发起。" }
```

上例为任务内部的 `error` 字段，任务查询本身仍返回 `200`。成功任务 `error: null`，`report` 结构如下：

```json
{
  "summary": "示例摘要：从业务、盈利质量与现金流角度组织已取得资料。",
  "sections": [{ "title": "业务概览", "text": "示例正文，实际内容必须能关联已获取资料。" }],
  "sources": [
    { "title": "示例公司公告", "url": "https://example.com/report", "publishedAt": "2026-10-27T18:00:00+08:00", "fetchedAt": "2026-10-29T18:30:20+08:00" }
  ],
  "dataQuality": "partial",
  "missingInformation": ["未取得可核实的最新现金流资料。"],
  "generatedAt": "2026-10-29T18:30:40+08:00"
}
```

- `sections` 是纯文本章节，不是可直接执行的 HTML。`sources` 为实际资料列表；来源 URL 仅允许 HTTP(S)，URL 与发布日期未知时可为 `null`，`fetchedAt` 必填，不得补造链接或日期。
- `dataQuality` 为 `complete` 或 `partial`，按该类分析所需资料判断。资料不足但仍可提供有限结论时成功并标记 `partial`，`missingInformation` 必须非空；完全无法分析时任务失败并说明缺失原因。
- 基本面、筹码和买卖条件共用该报告结构。来源、资料缺失和规则信号分别展示，避免把估算或 AI 文本当作确定事实。
- `dataDate` 仅指引用的行情快照日期；按需获取的资料可能晚于该日，须依据来源时间展示，报告不能被解释成历史时点已知结论。
