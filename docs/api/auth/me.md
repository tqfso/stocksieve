# 当前用户 `GET /auth/me`

[模块目录](README.md) · [接口总览](../README.md) · [通用约定](../common/conventions.md)

权限：登录用户。

成功 `200`，`data` 与[登录响应](login.md)中的 `data.user` 同结构。前端据此展示姓名、角色和导航；所有权限同时由服务端校验。
