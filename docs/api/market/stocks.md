# 个股详情 `GET /stocks/{stockId}`

[模块目录](README.md) · [接口总览](../README.md) · [通用约定](../common/conventions.md)

权限：登录用户。

可选查询参数 `snapshotId`。成功 `200`，`data` 内字段如下：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| snapshot | 对象 | 完整快照对象 |
| stock | 对象 | 与[机会列表](opportunities.md)相同的股票基础信息 |
| quote | 对象或 null | 与[机会列表](opportunities.md)相同；该日无可展示行情时为 null |
| signals | 数组 | 同一快照下全部入选策略的信号；没有入选时为空数组 |

基础信息和分类同样取自所选快照。股票不在该快照覆盖范围时返回 `404 STOCK_NOT_FOUND`；覆盖范围内仅当日行情不可用时，返回 `quote: null`。

`stock.board` 用于上市板块标签和筛选，`stock.exchange` 取 `SSE`（上海）、`SZSE`（深圳）、`BSE`（北京）。板块标识见[筛选选项](filters.md)。

`data.signals` 每项与[机会列表](opportunities.md)的 `data.items[].signal` 同结构，另含条件结果和失效说明。完整响应示例：

```json
{
  "data": {
    "snapshot": { "id": "snapshot_example", "dataDate": "2026-10-28", "publishedAt": "2026-10-28T18:30:00+08:00", "adjustment": "qfq", "strategyVersion": "v1" },
    "stock": {
      "stockId": "SZSE.002594", "code": "002594", "name": "示例股票", "exchange": "SZSE", "board": "SZSE_MAIN",
      "industry": { "id": "industry_auto", "name": "汽车" },
      "concepts": [{ "id": "concept_driving", "name": "智能驾驶" }]
    },
    "quote": { "price": 312.56, "change": 3.62, "turnover": 3.24, "amount": 7230000000, "totalMarketCap": 900000000000 },
    "signals": [
      {
        "id": "signal_example", "strategy": "short", "reason": "突破近期高点，成交额放大1.90倍，高于MA20 5.3%。",
        "metrics": { "breakoutReference": 303.46, "amountMultiple": 1.90, "ma20": 296.83, "ma20Distance": 5.3 },
        "conditionResults": [
          { "conditionId": "price_breakout", "passed": true },
          { "conditionId": "amount_expansion", "passed": true },
          { "conditionId": "ma20_distance", "passed": true }
        ],
        "invalidation": { "description": "后续收盘价低于入选时的突破参考价B。", "referencePrice": 303.46 }
      }
    ]
  },
  "requestId": "req_example"
}
```

信号是入选日快照，`conditionResults` 对应该版本策略的完整条件；失效描述用于后续复查，不表示已经计算了入选后的状态。短线的 `referencePrice` 为 B，波段及两类等待策略为 P，长线为 `null`。等待策略的入选信号表示当日仍在区间内等待，后续突破时需重新检查短线全部条件。

各策略 `metrics` 字段如下：价格单位为元；偏离和变化幅度为百分数；`amountMultiple` 为倍数，`pullbackAmountRatio` 为比值（`0.65` 表示65%）。

| 策略 | metrics 字段 |
| --- | --- |
| short | `breakoutReference`、`amountMultiple`、`ma20`、`ma20Distance` |
| swing | `ma20`、`ma60`、`ma60Change5`、`ma20Distance`、`pullbackAmountRatio`、`pullbackLow` |
| long | `ma20`、`ma60`、`ma120`、`ma60Change20`、`ma120Change20`、`ma60Distance` |
| trend_wait | `rangeLower`、`rangeUpper`、`rangeWidth`、`distanceToUpper`、`amountRatio`、`ma20`、`ma60`、`ma60Change5` |
| base_wait | `rangeLower`、`rangeUpper`、`rangeWidth`、`position120`、`amountRatio`、`ma20`、`ma60`、`low5`、`previousLow15` |

`ma60Change5` 表示 MA60 较 5 个交易日前的百分比变化；`ma60Change20`、`ma120Change20` 同理。参考价及均线均为所选快照的前复权价格。真实入选条件所需指标必须完整，不返回以缺失值凑成的入选信号。

`rangeWidth = (rangeUpper / rangeLower - 1) × 100`，`distanceToUpper = (rangeUpper - quote.price) / rangeUpper × 100`；`position120` 为此前120日高低区间位置的百分数（0～100），`amountRatio` 为近5日／近20日均额比值（0.65表示65%）。`low5` 为最近5日（含当日）最低价，`previousLow15` 为此前15日最低价。两类等待策略的区间上下沿均取入选日前20日，不含入选当日。
