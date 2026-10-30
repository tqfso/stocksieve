# 筛选选项 `GET /catalog/filters`

[模块目录](README.md) · [接口总览](../README.md) · [通用约定](../common/conventions.md)

权限：登录用户。

可选查询参数 `snapshotId`。成功 `200`：

```json
{
  "data": {
    "snapshotId": "snapshot_example",
    "boards": [
      { "id": "SSE_MAIN", "name": "沪市主板", "exchange": "SSE" },
      { "id": "SZSE_MAIN", "name": "深市主板", "exchange": "SZSE" },
      { "id": "CHINEXT", "name": "创业板", "exchange": "SZSE" },
      { "id": "STAR", "name": "科创板", "exchange": "SSE" },
      { "id": "BSE", "name": "北交所", "exchange": "BSE" }
    ],
    "industries": [{ "id": "industry_power", "name": "电力设备" }],
    "concepts": [{ "id": "concept_storage", "name": "储能" }],
    "coverage": { "board": "complete", "industry": "complete", "concepts": "partial", "turnover": "complete", "totalMarketCap": "complete" }
  },
  "requestId": "req_example"
}
```

分类 ID 在不同快照间保持稳定，名称用于展示；选项覆盖所选快照的全市场，与当前策略筛选无关。`coverage` 值为 `complete`、`partial`、`unavailable`；不可用选项返回空数组，前端禁用对应筛选并说明原因。请求不可用的筛选条件返回 `400 FILTER_UNAVAILABLE`；覆盖不完整时，仅匹配已知数据，不将未知值视为不属于该分类的确定结论。

`boards` 用于“上市板块”筛选，`exchange` 仅说明该板块所属交易所。个股的 `board` 由接口直接返回，前端使用其标识筛选和展示。
