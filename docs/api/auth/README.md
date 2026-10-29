# 登录与权限

[接口总览](../README.md) · [通用约定](../common/conventions.md)

## 登录 `POST /auth/login`

请求：

```json
{ "username": "admin", "password": "example-password" }
```

`username` 去除两侧空白并忽略大小写，格式遵循[新增用户约定](../admin/users.md)；密码按原文校验，必填且最长 32 位。账号或密码错误统一返回 `401 INVALID_CREDENTIALS`。

成功 `200`：

```json
{
  "accessToken": "example-token",
  "tokenType": "Bearer",
  "expiresAt": "2026-10-30T02:30:00+08:00",
  "user": {
    "id": "user_admin",
    "username": "admin",
    "name": "管理员",
    "role": "admin",
    "createdAt": "2026-10-28T09:00:00+08:00"
  }
}
```

会话有效期 8 小时；V1 到期重新登录。响应不返回密码。正式管理员凭据由部署时确定，不沿用模拟版账号密码。

## 当前用户 `GET /auth/me`

成功 `200`，`data` 为上述 `user` 对象。前端据此展示姓名、角色和导航；所有权限同时由服务端校验。

## 退出 `POST /auth/logout`

无请求体。成功 `204`，当前 token 立即失效，其他会话不受影响。

## 修改自身密码 `PUT /auth/password`

仅管理员，成功 `204`：

```json
{ "oldPassword": "example-password", "newPassword": "new-example-password" }
```

新密码 6～32 位，须与原密码不同；旧密码错误返回 `400 CURRENT_PASSWORD_INVALID`。成功后该账号的全部会话失效，前端回到登录页。普通用户返回 `403 FORBIDDEN`。确认密码由前端校验，不作为请求字段。
