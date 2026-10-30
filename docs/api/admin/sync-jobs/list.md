# 任务列表 `GET /admin/sync-jobs`

[模块目录](README.md) · [接口总览](../../README.md) · [通用约定](../../common/conventions.md)

权限：管理员。

可选 `kind`、`status`、`trigger`、`page`、`pageSize`。成功 `200`，`data` 为标准分页对象，`data.items` 按 `createdAt`、`id` 降序返回[完整任务](detail.md)。

[同步调度、预检与只读字段](../../common/sync-policy.md)
