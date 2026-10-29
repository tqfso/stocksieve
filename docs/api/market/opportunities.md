# 机会筛选

[接口总览](../README.md) · [通用约定](../common/conventions.md)

## 机会列表 `GET /opportunities`

| 查询参数 | 必填 | 说明 |
| --- | --- | --- |
| strategy | 是 | `short`、`swing`、`long` |
| snapshotId | 否 | 默认最新已发布快照 |
| search | 否 | 名称或代码包含匹配，去两侧空白，最长 50 字 |
| industryId、conceptId | 否 | 对应筛选选项 ID，各最多一个 |
| board | 否 | 上市板块枚举，单选；省略时包含全部板块 |
| minTurnover、maxTurnover | 否 | `0～100`，最小值不得大于最大值 |
| sort | 否 | `default`、`change`、`turnover`、`amount`，默认 `default` |
| order | 否 | 自定义排序时可选 `asc`、`desc`，默认 `desc`；`sort=default` 时不接受此参数 |
| page、pageSize | 否 | 标准分页 |

成功 `200`：

```json
{
  "snapshot": { "id": "snapshot_example", "dataDate": "2026-10-28", "publishedAt": "2026-10-28T18:30:00+08:00", "adjustment": "qfq", "strategyVersion": "v1" },
  "items": [
    {
      "stock": {
        "stockId": "SZSE.300750", "code": "300750", "name": "示例股票", "exchange": "SZSE", "board": "CHINEXT",
        "industry": { "id": "industry_power", "name": "电力设备" },
        "concepts": [{ "id": "concept_storage", "name": "储能" }]
      },
      "quote": { "price": 268.32, "change": 4.35, "turnover": 2.86, "amount": 8640000000 },
      "strategies": ["short", "long"],
      "signal": {
        "id": "signal_example", "strategy": "short",
        "reason": "突破此前 20 日高点，成交额放大 2.30 倍，高于 MA20 6.8%。",
        "metrics": { "breakoutReference": 260.50, "amountMultiple": 2.30, "ma20": 251.24, "ma20Distance": 6.8 }
      }
    }
  ],
  "page": 1, "pageSize": 20, "total": 1,
  "summary": { "industryCount": 1, "strategyCounts": { "short": 1, "swing": 0, "long": 1 } },
  "sort": { "field": "amountMultiple", "order": "desc" }
}
```

- 每行只对应一个股票与所选策略；`strategies` 是该股票在同一快照下符合的全部策略。
- `total`、`industryCount` 基于全部筛选结果，不受分页影响；无已知行业的股票不计入行业数量。
- `strategyCounts` 应用其他筛选条件，但忽略 `strategy`，用于三个标签的数量展示；同一股票可分别计入多个策略。
- 默认排序按策略说明执行；其他排序的缺失值始终排最后，值相同时按 `stockId` 升序，保证分页稳定。
- `sort` 响应返回实际生效的字段及方向。有效快照下无匹配结果返回 `items: []`，不是 `DATA_NOT_READY`。

`quote.price` 为快照日收盘价，`change` 为同一复权口径下较前一交易日收盘价的涨跌幅，`amount` 为当日原始成交额。`turnover` 采用当日流通 A 股数量为分母的换手率，无法取得或确认该口径时为 `null`。
