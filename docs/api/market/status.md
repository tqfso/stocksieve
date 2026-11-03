# 数据状态 `GET /market/status`

[模块目录](README.md) · [接口总览](../README.md) · [通用约定](../common/conventions.md)

权限：登录用户。

成功 `200`：

```json
{
  "data": {
    "initialized": true,
    "defaultSource": "sina",
    "marketBoards": ["SSE_MAIN", "SZSE_MAIN", "CHINEXT", "STAR"],
    "stockUpdatedAt": "2026-10-28T17:00:00+08:00",
    "catalogUpdatedAt": "2026-10-28T17:10:00+08:00",
    "latestTradingDate": "2026-10-29",
    "publishedSnapshot": {
      "id": "snapshot_example",
      "dataDate": "2026-10-28",
      "publishedAt": "2026-10-28T18:30:00+08:00",
      "adjustment": "qfq",
      "strategyVersion": "v1"
    },
    "coverage": { "totalStocks": 5223, "byExchange": { "SSE": 2318, "SZSE": 2905, "BSE": 0 } },
    "sources": [
      { "id": "baidu_concepts", "name": "百度股市通行业与题材", "available": true, "supportedSyncKinds": ["catalog"] },
      { "id": "sina", "name": "新浪行情（沪深四板块）", "available": true, "supportedSyncKinds": ["daily", "initialize"] }
    ]
  },
  "requestId": "req_example"
}
```

`latestTradingDate` 是截至请求时最近已收盘的交易日，无法确定时为 `null`；`publishedSnapshot` 无已发布数据时为 `null`。`coverage` 是已初始化的股票范围，未初始化时总数及各交易所数量为 `0`。渠道状态表示本系统当前配置和支持能力，示例不代表供应商的实际能力或可用性。

`initialized` 仅表示股票基础信息已准备。`stockUpdatedAt`、`catalogUpdatedAt` 分别为股票基础信息、分类关系最近一次同步成功时间，未成功或历史记录缺失时为 `null`；失败或其他类型任务不会改写该时间。`supportedSyncKinds` 按渠道能力返回 `initialize`、`catalog`、`daily` 的子集。

`marketBoards`为当前配置覆盖范围，当前先上线沪深四板块；北交所数量0表示未纳入，不表示市场没有北交所股票。已发布快照的历史范围以自身`boards`为准，不随配置改变。

`defaultSource`为默认渠道，页面优先选中支持当前任务的默认渠道；不支持时选其他可用渠道。任务仍显式提交`source`，已受理任务保留原渠道。
