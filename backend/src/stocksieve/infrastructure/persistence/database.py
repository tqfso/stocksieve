"""本文件管理 PostgreSQL 连接与短事务，不自动创建或修改数据库结构。"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Connection, make_url


def utc(value: datetime) -> datetime:
    """将数据库时间统一转换为 UTC。"""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class Database:
    """事务结束立即释放连接，网络取数和模型调用在事务外执行。"""

    def __init__(self, url: str | URL) -> None:
        parsed = make_url(url)
        if parsed.get_backend_name() != 'postgresql':
            raise ValueError('服务必须使用 PostgreSQL')
        self.engine = create_engine(parsed, pool_pre_ping=True, hide_parameters=True,
                                    connect_args={'connect_timeout': 5})

    @contextmanager
    def transaction(self, *, serialized: bool = False) -> Iterator[Connection]:
        with self.engine.begin() as connection:
            if serialized:
                # 同一业务库的任务领取和发布串行；隔离测试 schema 使用独立的锁。
                connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('stocksieve:' || current_schema()))"))
            yield connection

    def close(self) -> None:
        self.engine.dispose()
