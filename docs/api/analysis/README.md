# AI 分析报告接口

[报告设计](../../product/ai-analysis-design.md) · [行情与独立筹码](../market/chips.md)

当前统一生成七章模拟报告，支持Word/PDF下载；不调用真实模型或联网检索。

| 方法 | 路径 | 文档 |
| --- | --- | --- |
| POST | `/stocks/{stockId}/analyses` | [生成报告](create.md) |
| GET | `/analyses` | [本人历史](list.md) |
| GET | `/analyses/{analysisId}` | [报告详情](detail.md) |
| GET | `/analyses/{analysisId}/export` | [导出Word/PDF](export.md) |
