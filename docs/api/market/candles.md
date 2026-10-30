# K 线 `GET /stocks/{stockId}/candles`

[模块目录](README.md) · [接口总览](../README.md) · [通用约定](../common/conventions.md)

权限：登录用户。

| 查询参数 | 必填 | 说明 |
| --- | --- | --- |
| snapshotId | 否 | 默认最新快照；每次继续加载历史时沿用同一 ID |
| period | 否 | `day`、`week`、`year`，默认 `day` |
| before | 否 | 返回 `time` 严格早于此日期的聚合 K 线；首次省略 |
| limit | 否 | 最近多少根，默认 `240`，整数 `1～1000` |

成功 `200`：

```json
{
  "data": {
    "stockId": "SZSE.300750", "snapshotId": "snapshot_example", "dataDate": "2026-10-28",
    "period": "day", "adjustment": "qfq",
    "items": [
      { "time": "2026-10-28", "open": 259.20, "high": 270.10, "low": 258.20, "close": 268.32, "volume": 32000000, "amount": 8640000000, "ma5": 261.60, "ma10": 257.30, "ma20": 251.24, "ma60": 238.72, "ma120": 226.40, "isComplete": true }
    ],
    "hasMore": true,
    "nextBefore": "2026-10-28"
  },
  "requestId": "req_example"
}
```

- 选取指定边界前最近 `limit` 根后，按 `time` 升序返回。`hasMore` 表示仍有更早数据；有更早数据时 `nextBefore` 为本页最早日期，否则为 `null`。空结果返回 `hasMore: false`、`nextBefore: null`。
- 日线按交易日历提供实际可用记录，缺失记录不伪造。周线按自然周、年线按自然年聚合；`time` 为该根内最后一个实际有行情的交易日。
- 聚合使用首开、末收、最高、最低，成交量和成交额求和。全部行情截至快照日；当周 / 当年仍有尚未收盘的交易日时，`isComplete: false`，按交易日历判定；日线为已收盘数据。
- `ma5`、`ma10`、`ma20`、`ma60`、`ma120` 是当前 K 线周期的收盘均线，覆盖现有策略需要的所有周期。包含本根，使用分页窗口之外的历史计算；历史不足或必需窗口内记录缺失时为 `null`。周 / 年线均线不能代替选股规则中的日线均线。
- OHLC 与均线价格、成交额 `amount` 的单位为人民币元，成交量 `volume` 为股。`qfq` 为唯一价格复权口径；成交量、成交额保持原始值。
