# 用户列表 `GET /admin/users`

[模块目录](README.md) · [接口总览](../../README.md) · [通用约定](../../common/conventions.md)

权限：管理员。

可选 `search`（账号或名称包含匹配，最长 50 字）、`role`（`admin` / `user`）、`page`、`pageSize`。成功 `200`，`data` 为标准分页对象，`data.items` 每项与[登录响应](../../auth/login.md)的 `data.user` 同结构，按创建时间降序、相同时间按 `id` 降序。密码在任何用户响应中均不返回。
