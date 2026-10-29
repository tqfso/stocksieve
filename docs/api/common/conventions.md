# 通用接口约定

[接口总览](../README.md)

## 请求与响应

- 接口前缀：`/api/v1`；请求、响应采用 UTF-8 JSON，字段使用 camelCase。
- 除登录外均需 `Authorization: Bearer <accessToken>`。正式部署通过 HTTPS 访问。
- 日期使用 `YYYY-MM-DD`，按 `Asia/Shanghai` 解释；时间使用带时区的格式，例如 `2026-10-29T18:30:00+08:00`。
- ID 为不透明字符串；股票使用 `stockId` 唯一标识，例如 `SZSE.300750`。股票 `code` 始终为字符串，保留前导零。
- 未知参数、非法枚举、无效日期、超限字段及不支持的请求字段返回 `400`，不静默忽略。
- 可选查询参数省略或传空字符串时采用该接口的默认行为；非空参数必须满足约束。筛选条件默认不限制，必填参数不可为空。
- 所有示例均为虚构数据，不代表真实市场、账号或资料。

成功响应：

```json
{
  "data": {},
  "requestId": "req_example"
}
```

失败响应：

```json
{
  "error": {
    "code": "INVALID_ARGUMENT",
    "message": "最低换手率不能大于最高换手率。",
    "details": { "field": "minTurnover" }
  },
  "requestId": "req_example"
}
```

`error.code` 用于程序判断，`message` 用于界面提示；`details` 为对象，无补充信息时为 `{}`。响应头 `X-Request-ID` 与正文 `requestId` 一致。`204` 响应无正文。

各业务文档中的响应示例仅展示 `data` 内容，均使用上述外层结构。

## 枚举与单位

| 字段 | 取值 / 单位 |
| --- | --- |
| role | `admin` 管理员、`user` 普通用户 |
| exchange | `SSE` 上海、`SZSE` 深圳、`BSE` 北京 |
| board | `SSE_MAIN` 沪市主板、`SZSE_MAIN` 深市主板、`CHINEXT` 创业板、`STAR` 科创板、`BSE` 北交所 |
| strategy | `short` 短线、`swing` 波段、`long` 长线 |
| period | `day` 日线、`week` 周线、`year` 年线 |
| kind（分析） | `fundamental` 基本面、`chips` 筹码、`trade` 买卖条件 |
| kind（同步） | `initialize` 初始化、`daily` 收盘同步 |
| source | `sina`、`baostock`；当前实际可用渠道以[数据状态接口](../market/status.md)为准 |
| status（任务） | `queued` 排队、`running` 进行中、`succeeded` 成功、`failed` 失败 |
| 价格 / 成交额 amount | 人民币元；例如 `amount: 8640000000` 表示 86.4 亿元 |
| totalMarketCap、minMarketCap、maxMarketCap | 总市值及区间边界，人民币元；例如 `10000000000` 表示 100 亿元 |
| 成交量 volume | 股，不是手 |
| change、turnover、均线偏离及变化幅度 | 百分数，例如 `4.35` 表示 `4.35%` |
| amountMultiple、pullbackAmountRatio | 倍数 / 比值，例如 `1.8` 表示 1.8 倍，`0.65` 表示 65% |

行情或指标无法取得时返回 `null`，分类无归属时对象为 `null`、数组为 `[]`；不能以 `0` 替代缺失值。真实零值仍返回 `0`。

## 分页

普通列表使用 `page`、`pageSize`，默认 `1`、`20`；`page >= 1`，`1 <= pageSize <= 100`，均为整数。超过末页返回空 `items`，保留实际 `total`。

```json
{ "items": [], "page": 1, "pageSize": 20, "total": 0 }
```

[K 线](../market/candles.md)使用日期游标，[任务日志](../admin/sync-jobs.md)使用日志游标。无匹配结果属于成功响应，与数据尚未就绪、请求失败分别处理。
