"""本文件提供受调用方事务约束的通用持久化操作，避免业务模块互相借用SQL工具。"""

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.engine import Connection


def upsert(connection: Connection, table: Any, keys: dict[str, Any], values: dict[str, Any]) -> None:
    """调用者持有短事务锁，更新只作用于指定版本的记录。"""
    predicate = [table.c[key] == value for key, value in keys.items()]
    if connection.execute(select(next(iter(table.primary_key.columns))).where(*predicate)).first():
        connection.execute(update(table).where(*predicate).values(**values))
    else:
        connection.execute(table.insert().values(**keys, **values))
