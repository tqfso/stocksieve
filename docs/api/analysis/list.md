# 分析历史 `GET /analyses`

[模块目录](README.md) · [接口总览](../README.md) · [通用约定](../common/conventions.md)

权限：登录用户。

查询参数：可选 `stockId`、`kind`、`strategy`、`snapshotId`、`status`、`page`、`pageSize`。`strategy` 仅用于 `kind=trade` 的历史筛选；其他类型传入时返回 `400 INVALID_ARGUMENT`。仅返回当前用户自己的记录，按 `createdAt` 降序、相同时间按 `id` 降序。

成功 `200`，`data` 为标准分页对象；`data.items` 每项与[发起分析](create.md)响应的 `data` 同结构，但列表省略 `report`，查看内容使用[分析详情](detail.md)。报告历史下拉框可传 `stockId`、`kind`、`status=succeeded`；如仅看当前快照，再传 `snapshotId`。
