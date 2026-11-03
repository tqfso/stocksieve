# AI 分析报告接口

[报告设计](../../product/ai-analysis-design.md) · [行情与独立筹码](../market/chips.md)

统一生成七章报告，支持真实模型与离线模拟，以及Word/PDF下载。真实模式读取公开原文并使用金融计算；历史报告保留自身模式与引用。

| 方法 | 路径 | 文档 |
| --- | --- | --- |
| POST | `/stocks/{stockId}/analyses` | [生成报告](create.md) |
| GET | `/analyses` | [本人历史](list.md) |
| GET | `/analyses/{analysisId}` | [报告详情](detail.md) |
| GET | `/analyses/{analysisId}/export` | [导出Word/PDF](export.md) |
