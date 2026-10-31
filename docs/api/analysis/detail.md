# 分析详情 `GET /analyses/{analysisId}`

[模块目录](README.md) · [接口总览](../README.md) · [通用约定](../common/conventions.md)

权限：分析所属用户。

成功 `200`，`data` 返回完整任务。仅所属用户可访问，管理员也不能查看他人报告；无权访问与不存在均返回 `404 ANALYSIS_NOT_FOUND`。

任务失败时仍返回 `200`，`data.status=failed`、`data.report=null`，`data.error` 包含 `code`（例如 `ANALYSIS_TIMEOUT`）和可读原因；这与 HTTP 请求失败时的顶层 `error` 不同。

### 任务阶段

`stage` 为必填的用户可见业务阶段，与 `status` 分别返回：

| stage | 展示 | 对应 status |
| --- | --- | --- |
| queued | 排队中 | queued |
| researching | 获取资料 | running |
| analyzing | 分析研判 | running |
| writing | 整理报告 | running |
| completed | 报告已生成 | succeeded |

失败时保留最后阶段，`status=failed`；`report=null` 只表示该次任务无新报告，不删除历史。获取资料阶段完全无法继续时，可返回 `ANALYSIS_SOURCES_UNAVAILABLE`。分析中可再次进入 `researching` 补充资料；前端展示最新阶段，不能据阶段推定固定耗时或展示内部推理过程。

### 成功报告

任务成功时的完整响应示例：

```json
{
  "data": {
    "id": "analysis_example", "stockId": "SZSE.300750", "kind": "fundamental", "strategy": null,
    "snapshotId": "snapshot_example", "dataDate": "2026-10-28",
    "status": "succeeded", "stage": "completed", "progress": 100,
    "createdAt": "2026-10-29T18:30:00+08:00", "startedAt": "2026-10-29T18:30:02+08:00", "finishedAt": "2026-10-29T18:30:40+08:00",
    "error": null,
    "report": {
      "title": "示例股票 · 基本面分析",
      "overview": {
        "decision": { "action": "wait", "label": "暂缓买入", "reason": "股东减持风险尚未核实，先等待资料补齐。" },
        "opportunities": ["已有经营资料显示收入和利润增长。"],
        "risks": ["股东减持计划及实施进展未核验。"]
      },
      "summary": "近12个月营收同比增长18.6%，净利润同比增长22.4%，经营现金流能够覆盖净利润。后续需要验证增长和现金回收能否延续。",
      "metrics": [
        { "label": "营收同比", "value": "+18.6%", "detail": "近12个月" },
        { "label": "现金流／净利润", "value": "1.12倍", "detail": "经营现金流覆盖情况" }
      ],
      "sections": [{ "title": "盈利质量与现金流", "text": "经营现金流／净利润为1.12倍，利润形成了相应现金流，后续需关注回款和营运资金占用变化。" }],
      "sources": [
        { "title": "示例公司公告", "url": "https://example.com/report", "publishedAt": "2026-10-27T18:00:00+08:00", "fetchedAt": "2026-10-29T18:30:20+08:00" }
      ],
      "dataQuality": "partial",
      "missingInformation": ["未取得最新分部经营资料。"],
      "materialsAsOfAt": "2026-10-29T18:30:20+08:00",
      "generatedAt": "2026-10-29T18:30:40+08:00"
    }
  },
  "requestId": "req_example"
}
```

- `sections` 是纯文本章节，不是可直接执行的 HTML。`sources` 为实际资料列表；来源 URL 仅允许 HTTP(S)，URL 与发布日期未知时可为 `null`，`fetchedAt` 必填，不得补造链接或日期。
- `title` 为报告标题，`metrics` 为关键指标卡片，含 `label`、`value`、`detail`；没有可展示的已核实指标时返回空数组，不填造数值。上述数据仅为虚构接口示例。
- `overview` 为默认展示的精简结果：`decision` 含 `action`（`consider` / `wait` / `avoid`）、明确的建议 `label` 及一句 `reason`；`opportunities`、`risks` 为短句数组，默认各展示一条。详细正文及来源按需展开。
- 买卖建议可附 `overview.pricePlan`，含 `waiting`、`entryLabel`、`entryPrice`、`entryCondition`、`exitPrice`、`exitCondition`、`considerIf`、`avoidIf`。价位字段为含单位的展示文本，条件与适合/暂不适合的情况在详细依据中查看；价位缺乏依据时省略计划，并在当前建议说明等待或暂不买。
- 基本面指标明确标注营业收入（TTM）、归母净利润（TTM）及 PE（TTM）。TTM 为最近四个季度的滚动12个月；PE采用同币种、同单位总市值除以归母净利润（TTM）。盈利为零、为负或数据缺失时显示无有效倍数，不以零或负 PE 误导估值判断。
- `dataQuality` 为 `complete` 或 `partial`，按该类分析所需资料判断。资料不足但仍可提供有限结论时成功并标记 `partial`，`missingInformation` 必须非空；完全无法分析时任务失败并说明缺失原因。
- 基本面、筹码和买卖建议共用该报告结构。来源、资料缺失和规则信号分别展示，避免把估算或 AI 文本当作确定事实。
- `dataDate` 仅指引用的行情快照日期；按需获取的资料可能晚于该日，须依据来源时间展示，报告不能被解释成历史时点已知结论。
- `materialsAsOfAt` 为本次资料集合冻结时间，必填且不晚于 `generatedAt`；前端以“资料获取截至”展示，与“行情截至”的 `dataDate` 分开。来源的 `publishedAt` 和 `fetchedAt` 分别表示发布时间与获取时间，发布时间未知时明确展示未知。

### 筹码分析图形数据

筹码报告可附带 `data.report.chipDistribution`，仅用于页面分析结果内绘图，无独立的资金或筹码接口。缺少可核实分布时返回 `null` 或省略该字段。

| 字段 | 说明 |
| --- | --- |
| currentPrice | 对应行情快照的收盘价，单位元 |
| bins | 按价格升序的档位，每项为 `{lower, upper, price, ratio}`，分别为价格区间下界、上界、代表价与筹码占比（%） |
| averageCost、profitableRatio | 估算平均成本，以及收盘价及以下的估算筹码占比 |
| cost70、cost90 | 中央70%和90%的成本区间，分别按第15～85、第5～95百分位计算 |
| support、resistance | 与 `bins` 同结构，标识现价下方、上方的主要筹码参考档位 |

档位连续且占比合计100%；价格与当前快照使用统一复权口径。图中支撑、压力和报告文字必须引用同一份数据，明确标为估算参考，不能保证价格反弹或受阻。占比表示成本区间内的估算筹码，不等同当日成交量或真实股东持股数量。

现价某一侧没有正占比档位时，该侧 `support` 或 `resistance` 为 `null`，不得拿空档位补造支撑或压力；后续前端对接需展示该侧暂无有效参考。
