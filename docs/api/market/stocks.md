# 个股详情与策略信号

[接口总览](../README.md) · [通用约定](../common/conventions.md)

## 个股详情 `GET /stocks/{stockId}`

可选查询参数 `snapshotId`。成功 `200`，返回：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| snapshot | 对象 | 完整快照对象 |
| stock | 对象 | 与[机会列表](opportunities.md)相同的股票基础信息 |
| quote | 对象或 null | 与[机会列表](opportunities.md)相同；该日无可展示行情时为 null |
| signals | 数组 | 同一快照下全部入选策略的信号；没有入选时为空数组 |

基础信息和分类同样取自所选快照。股票不在该快照覆盖范围时返回 `404 STOCK_NOT_FOUND`；覆盖范围内仅当日行情不可用时，返回 `quote: null`。

`stock.board` 用于上市板块标签和筛选，`stock.exchange` 保留所属交易所信息；枚举见通用约定。

`signals` 的每项包含[机会列表](opportunities.md)中的 `id`、`strategy`、`reason`、`metrics`，并补充：

```json
{
  "conditionResults": [
    { "conditionId": "price_breakout", "passed": true },
    { "conditionId": "amount_expansion", "passed": true },
    { "conditionId": "ma20_distance", "passed": true }
  ],
  "invalidation": { "description": "后续收盘价低于入选时的突破参考价 B。", "referencePrice": 260.50 }
}
```

信号是入选日快照，`conditionResults` 对应该版本策略的完整条件；失效描述用于后续复查，不表示已经计算了入选后的状态。短线和波段 `referencePrice` 分别为 B、P，长线为 `null`。

各策略 `metrics` 字段如下，字段命名及单位遵循通用约定：

| 策略 | metrics 字段 |
| --- | --- |
| short | `breakoutReference`、`amountMultiple`、`ma20`、`ma20Distance` |
| swing | `ma20`、`ma60`、`ma60Change5`、`ma20Distance`、`pullbackAmountRatio`、`pullbackLow` |
| long | `ma20`、`ma60`、`ma120`、`ma60Change20`、`ma120Change20`、`ma60Distance` |

`ma60Change5` 表示 MA60 较 5 个交易日前的百分比变化；`ma60Change20`、`ma120Change20` 同理。参考价及均线均为所选快照的前复权价格。真实入选条件所需指标必须完整，不返回以缺失值凑成的入选信号。
