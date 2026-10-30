# 策略说明 `GET /strategies`

[模块目录](README.md) · [接口总览](../README.md) · [通用约定](../common/conventions.md)

权限：登录用户。

无需行情快照，即使尚未初始化也可查询。成功 `200`：

```json
{
  "data": {
    "version": "v1",
    "items": [
      {
        "id": "short",
        "name": "短线",
        "subtitle": "放量突破",
        "observationPeriod": "3～10 个交易日",
        "description": "关注突破近期高点、成交额同步放大的机会。",
        "conditions": [
          { "id": "price_breakout", "label": "价格突破", "description": "收盘价超过此前 20 个交易日最高价。" },
          { "id": "amount_expansion", "label": "成交放大", "description": "成交额至少为此前 5 日均值的 1.5 倍。" },
          { "id": "ma20_distance", "label": "均线偏离受限", "description": "收盘价高于 MA20，偏离不超过 10%。" }
        ],
        "invalidationDescription": "后续收盘价低于入选时的突破参考价 B。",
        "defaultSort": { "field": "amountMultiple", "order": "desc" }
      }
    ]
  },
  "requestId": "req_example"
}
```

实际返回五类策略：`short`、`swing`、`long`、`trend_wait`（趋势埋伏）、`base_wait`（低位筑底），规则内容以[策略文档](../../product/strategies/README.md)为准。V1 快照均使用 `v1` 规则；波段默认按 `ma20Distance` 升序，长线按 `ma60Distance` 升序，趋势埋伏按 `distanceToUpper` 升序，低位筑底按 `position120` 升序。

条件 ID 固定如下，与[个股详情](stocks.md)中的 `conditionResults.conditionId` 一一对应：

| 策略 | 条件 ID（按展示顺序） |
| --- | --- |
| short | `price_breakout`、`amount_expansion`、`ma20_distance` |
| swing | `uptrend`、`pullback_near_ma20`、`pullback_amount`、`stabilization` |
| long | `ma_alignment`、`ma60_rising`、`ma120_rising`、`ma60_distance` |
| trend_wait | `trend_holding`、`range_contraction`、`amount_contraction`、`near_range_upper` |
| base_wait | `trend_unrepaired`、`low_position`、`range_contraction`、`no_new_low`、`amount_contraction`、`inside_range` |
