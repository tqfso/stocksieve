# 机会列表 `GET /opportunities`

[模块目录](README.md) · [接口总览](../README.md) · [通用约定](../common/conventions.md)

权限：登录用户。

| 查询参数 | 必填 | 说明 |
| --- | --- | --- |
| strategy | 是 | `short`、`swing`、`long`、`trend_wait`（趋势埋伏）、`base_wait`（低位筑底） |
| snapshotId | 否 | 默认最新已发布快照 |
| search | 否 | 名称或代码包含匹配，去两侧空白，最长 50 字 |
| industryId、conceptId | 否 | 对应筛选选项 ID，各最多一个 |
| board | 否 | 上市板块枚举，单选；省略时包含全部板块 |
| minTurnover、maxTurnover | 否 | `0～100`，最小值不得大于最大值 |
| minMarketCap、maxMarketCap | 否 | 总市值区间，单位元，均不得小于 `0`；最小值不得大于最大值，可仅传一侧，包含边界 |
| sort | 否 | `default`、`change`、`turnover`、`amount`、`totalMarketCap`，默认 `default` |
| order | 否 | 自定义排序时可选 `asc`、`desc`，默认 `desc`；`sort=default` 时不接受此参数 |
| page、pageSize | 否 | 标准分页 |

成功 `200`：

```json
{
  "data": {
    "snapshot": { "id": "snapshot_example", "dataDate": "2026-10-28", "publishedAt": "2026-10-28T18:30:00+08:00", "adjustment": "qfq", "strategyVersion": "v1" },
    "items": [
      {
        "stock": {
          "stockId": "SZSE.300750", "code": "300750", "name": "示例股票", "exchange": "SZSE", "board": "CHINEXT",
          "industry": { "id": "industry_power", "name": "电力设备" },
          "concepts": [{ "id": "concept_storage", "name": "储能" }]
        },
        "quote": { "price": 268.32, "change": 4.35, "turnover": 2.86, "amount": 8640000000, "totalMarketCap": 1180000000000 },
        "strategies": ["short", "long"],
        "signal": {
          "id": "signal_example", "strategy": "short",
          "reason": "突破此前 20 日高点，成交额放大 2.30 倍，高于 MA20 6.8%。",
          "metrics": { "breakoutReference": 260.50, "amountMultiple": 2.30, "ma20": 251.24, "ma20Distance": 6.8 }
        }
      }
    ],
    "page": 1, "pageSize": 20, "total": 1,
    "summary": { "industryCount": 1, "strategyCounts": { "short": 1, "swing": 0, "long": 1, "trend_wait": 0, "base_wait": 0 } },
    "sort": { "field": "amountMultiple", "order": "desc" }
  },
  "requestId": "req_example"
}
```

- 每行只对应一个股票与所选策略；`strategies` 是该股票在同一快照下符合的全部策略。
- `total`、`industryCount` 基于全部筛选结果，不受分页影响；无已知行业的股票不计入行业数量。
- `strategyCounts` 应用其他筛选条件，但忽略 `strategy`，返回五类策略的计数；同一股票可分别计入多个策略，不相加作为去重股票总数。
- 默认排序按策略说明执行；其他排序的缺失值始终排最后，值相同时按 `stockId` 升序，保证分页稳定。
- `sort` 响应返回实际生效的字段及方向。有效快照下无匹配结果返回 `items: []`，不是 `DATA_NOT_READY`。

`data.items[].quote` 的价格、成交额和总市值均以人民币元返回；`change`、`turnover` 为百分数，例如 `4.35` 表示4.35%。`quote.price` 为快照日收盘价，`change` 为同一复权口径下较前一交易日收盘价的涨跌幅，`amount` 为当日原始成交额。`turnover` 采用当日流通 A 股数量为分母的换手率，无法取得或确认该口径时为 `null`。

`quote.totalMarketCap` 为快照日总市值，采用未复权价格口径，不是流通市值，也不由图表前复权价格直接推算。未知时返回 `null`；设置任一市值边界时，排除市值未知的股票，不将其按零值匹配。请求 `minMarketCap=10000000000&maxMarketCap=50000000000` 表示筛选 100～500 亿元。
