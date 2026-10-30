# 新增普通用户 `POST /admin/users`

[模块目录](README.md) · [接口总览](../../README.md) · [通用约定](../../common/conventions.md)

权限：管理员。

请求：

```json
{ "username": "reviewer", "name": "评审用户", "password": "example-password" }
```

- `username` 去两侧空白后为 3～20 位字母、数字或下划线，以字母开头；按小写保存，忽略大小写唯一。重复返回 `409 USERNAME_EXISTS`。
- `name` 去两侧空白后为 1～20 字；`password` 为 6～32 位，不去空白、不截断。
- 成功 `201`，`data` 返回[用户对象](../../auth/login.md)（与 `data.user` 同结构），`role` 固定为 `user`。请求不能指定角色；V1 不提供创建管理员、删除用户或重置他人密码的接口。
