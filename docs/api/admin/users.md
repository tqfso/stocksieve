# 管理员用户管理

[接口总览](../README.md) · [通用约定](../common/conventions.md)

## 用户列表 `GET /admin/users`

可选 `search`（账号或名称包含匹配，最长 50 字）、`role`、`page`、`pageSize`。成功 `200`，标准分页；每项为[登录响应](../auth/README.md)中的用户对象，按创建时间降序、相同时间按 `id` 降序。密码在任何用户响应中均不返回。

## 新增普通用户 `POST /admin/users`

请求：

```json
{ "username": "reviewer", "name": "评审用户", "password": "example-password" }
```

- `username` 去两侧空白后为 3～20 位字母、数字或下划线，以字母开头；按小写保存，忽略大小写唯一。重复返回 `409 USERNAME_EXISTS`。
- `name` 去两侧空白后为 1～20 字；`password` 为 6～32 位，不去空白、不截断。
- 成功 `201`，返回用户对象，`role` 固定为 `user`。请求不能指定角色；V1 不提供创建管理员、删除用户或重置他人密码的接口。
