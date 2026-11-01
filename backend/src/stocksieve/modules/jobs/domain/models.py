"""本文件定义任务租约值对象，持有者与执行代次共同标识写入资格。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Lease:
    """任务ID、持有者与执行代次共同标识写入资格。"""

    id: str
    owner: str
    attempt: int
