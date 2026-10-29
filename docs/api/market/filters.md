# 筛选选项

[接口总览](../README.md) · [通用约定](../common/conventions.md)

## 筛选选项 `GET /catalog/filters`

可选查询参数 `snapshotId`。成功 `200`：

```json
{
  "snapshotId": "snapshot_example",
  "exchanges": [{ "id": "SZSE", "name": "深圳" }],
  "industries": [{ "id": "industry_power", "name": "电力设备" }],
  "concepts": [{ "id": "concept_storage", "name": "储能" }],
  "coverage": { "industry": "complete", "concepts": "partial", "turnover": "complete" }
}
```

分类 ID 在不同快照间保持稳定，名称用于展示；选项覆盖所选快照的全市场，与当前策略筛选无关。`coverage` 值为 `complete`、`partial`、`unavailable`；不可用选项返回空数组，前端禁用对应筛选并说明原因。请求不可用的筛选条件返回 `400 FILTER_UNAVAILABLE`；覆盖不完整时，仅匹配已知数据，不将未知值视为不属于该分类的确定结论。
