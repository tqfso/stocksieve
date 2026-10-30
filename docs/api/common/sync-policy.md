# 同步调度与预检约定

[接口总览](../README.md) · [异步任务](tasks.md) · [业务需求](../../requirements.md)

- 自动任务使用默认数据源，目标日期为北京时间当天，不回退到前一交易日。同日已有未完成任务时关联该任务；其他同步任务串行排队，每天只记录一次计划触发。
- 自动、手动收盘同步共用预检；股票基础信息和分类任务无需预检。每轮检查全部五个上市板块的数据日期和收盘数据可用性，不能仅凭单只股票或非空响应判断整个板块就绪。
- 所有应开市板块就绪后才同步，再做完整性校验。等待、跳过和失败均保留原快照。
- 分类更新频率遵循[数据管理](../../requirements.md#2-数据管理)；自动和手动收盘同步均不请求行业、题材接口，复用最近成功的分类关系，分类未知不阻塞行情任务。

| 板块预检状态 | 处理 |
| --- | --- |
| ready | 数据已就绪 |
| pending | 尚未更新，等待重试 |
| market_closed | 交易日历确认休市，无需等待；全部休市时跳过任务 |
| unknown | 暂无法确认，等待重试，不推定休市 |
| unsupported | 来源不支持该开市板块，直接失败 |

默认每 **10 分钟**重试，从首次未就绪起最多等待 **4 小时**，均可配置。等待使用原任务 ID 和目标日期，重启后继续，不持续占用执行名额；超时以 `DATA_READY_TIMEOUT` 失败，日志列明未就绪板块。

等待时为 `status=queued`、`error=null`、`finishedAt=null`；就绪后继续执行。同步任务包含以下只读字段：

| 字段 | 说明 |
| --- | --- |
| waitReason | 等待数据时为 `BOARD_DATA_NOT_READY`，其他情况为 null |
| nextRetryAt | 下次预检时间，非等待状态为 null |
| retryDeadlineAt | 等待截止时间；尚未等待时为 null，终态保留用于追溯 |
| retryCount | 已执行的重试预检次数，不含首次 |
| precheck | 最近一轮 `{checkedAt, boards}`，尚未预检时为 null |
| precheck.boards | 每板块一项 `{board, status, latestDataDate, message}`；latestDataDate 未知时为 null，message 说明原因 |
