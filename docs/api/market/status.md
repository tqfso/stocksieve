# 数据状态 `GET /market/status`

[模块目录](README.md) · [接口总览](../README.md) · [通用约定](../common/conventions.md)

权限：登录用户。

成功 `200`：

```json
{
  "data": {
    "initialized": true,
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
    "coverage": { "totalStocks": 5348, "byExchange": { "SSE": 2300, "SZSE": 2800, "BSE": 248 } },
    "sources": [
      { "id": "sina", "name": "Sina", "available": true, "supportedSyncKinds": ["initialize", "daily"] },
      { "id": "baostock", "name": "Baostock", "available": false, "supportedSyncKinds": [] },
      { "id": "catalog_provider", "name": "分类渠道示例", "available": true, "supportedSyncKinds": ["catalog"] }
    ]
  },
  "requestId": "req_example"
}
```

`latestTradingDate` 是截至请求时最近已收盘的交易日，无法确定时为 `null`；`publishedSnapshot` 无已发布数据时为 `null`。`coverage` 是已初始化的股票范围，未初始化时总数及各交易所数量为 `0`。渠道状态表示本系统当前配置和支持能力，示例不代表供应商的实际能力或可用性。

`initialized` 仅表示股票基础信息已准备。`stockUpdatedAt`、`catalogUpdatedAt` 分别为股票基础信息、分类关系最近一次同步成功时间，未成功或历史记录缺失时为 `null`；失败或其他类型任务不会改写该时间。`supportedSyncKinds` 按渠道能力返回 `initialize`、`catalog`、`daily` 的子集。
