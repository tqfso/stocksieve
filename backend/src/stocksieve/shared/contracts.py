"""本文件定义应用输入契约的公共校验基类，不依赖HTTP框架。"""

from pydantic import BaseModel, ConfigDict


class Input(BaseModel):
    """应用输入统一拒绝未声明字段，保持接口与后台命令校验一致。"""

    model_config = ConfigDict(extra='forbid')
