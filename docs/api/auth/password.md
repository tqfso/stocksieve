# 修改自身密码 `PUT /auth/password`

[模块目录](README.md) · [接口总览](../README.md) · [通用约定](../common/conventions.md)

权限：管理员。

仅管理员。请求体：

```json
{ "oldPassword": "example-password", "newPassword": "new-example-password" }
```

成功 `204`，无正文。新密码 6～32 位，须与原密码不同；旧密码错误返回 `400 CURRENT_PASSWORD_INVALID`。成功后该账号的全部会话失效，前端回到登录页。普通用户返回 `403 FORBIDDEN`。确认密码由前端校验，不作为请求字段。
