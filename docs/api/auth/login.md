# 登录 `POST /auth/login`

[模块目录](README.md) · [接口总览](../README.md) · [通用约定](../common/conventions.md)

权限：无需登录。

请求：

```json
{ "username": "admin", "password": "example-password" }
```

`username` 去除两侧空白并忽略大小写，格式遵循[新增用户约定](../admin/users/create.md)；密码按原文校验，必填且最长 32 位。账号或密码错误统一返回 `401 INVALID_CREDENTIALS`。

成功 `200`：

```json
{
  "data": {
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
  },
  "requestId": "req_example"
}
```

会话有效期 8 小时；V1 到期重新登录。响应不返回密码。正式管理员凭据由部署时确定，不沿用模拟版账号密码。
