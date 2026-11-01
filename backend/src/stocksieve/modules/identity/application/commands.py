"""本文件定义登录、改密与新增用户的应用输入契约。"""

from pydantic import Field

from stocksieve.shared.contracts import Input


class LoginInput(Input):
    username: str
    password: str = Field(min_length=1, max_length=32)


class UserInput(Input):
    username: str
    name: str
    password: str = Field(min_length=6, max_length=32)


class PasswordInput(Input):
    oldPassword: str = Field(min_length=1, max_length=32)
    newPassword: str = Field(min_length=6, max_length=32)
